package slreplay_test

import (
	"encoding/json"
	"fmt"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
)

type captureData struct {
	file, symbol string
	cfg          sessionlevel.Config
	m5, h1       []market.Candle
}

func resolveDoc(t testing.TB) *config.Document {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

func loadCapture(t testing.TB, doc *config.Document, file string) captureData {
	t.Helper()
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", file))
	if err != nil {
		t.Fatal(err)
	}
	m5, err := capture.Candles(market.M5)
	if err != nil {
		t.Fatal(err)
	}
	h1, err := capture.Candles(market.H1)
	if err != nil {
		t.Fatal(err)
	}
	params, err := sessionParamsFor(doc, string(capture.Symbol))
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := sessionlevel.ParseConfig(params)
	if err != nil {
		t.Fatal(err)
	}
	return captureData{file: file, symbol: string(capture.Symbol), cfg: cfg, m5: m5, h1: h1}
}

// TestSessionLevelHasNoFutureDependenceOnRealCaptures proves, bar by bar on every
// real capture, that the decision made with the future present equals the decision
// made with only the past, and that replacing the future (of both timeframes) with
// garbage changes nothing.
func TestSessionLevelHasNoFutureDependenceOnRealCaptures(t *testing.T) {
	doc := resolveDoc(t)
	var wg sync.WaitGroup
	for _, c := range captures {
		wg.Add(1)
		go func(file string) {
			defer wg.Done()
			data := loadCapture(t, doc, file)
			rng := rand.New(rand.NewSource(1))
			evaluations, episodes := 0, 0
			for i := range data.m5 {
				now := data.m5[i].Time + 300
				var closedH1 []market.Candle
				for _, h := range data.h1 {
					if h.Time+3600 <= now {
						closedH1 = append(closedH1, h)
					}
				}
				asOf := sessionlevel.DetectAsOf(data.cfg, sessionlevel.Input{M5: data.m5, H1: data.h1}, i)
				live := sessionlevel.Detect(data.cfg, sessionlevel.Input{M5: data.m5[:i+1], H1: closedH1})
				if !reflect.DeepEqual(asOf, live) {
					t.Errorf("%s candle %d: the decision with the future present differs from the prefix decision", file, i)
					return
				}
				if i%9 == 0 {
					mutated := append([]market.Candle(nil), data.m5...)
					for k := i + 1; k < len(mutated); k++ {
						p := 100 + rng.Float64()*100
						mutated[k] = market.Candle{Time: mutated[k].Time, Open: p, High: p + 5, Low: p - 5, Close: p + 1}
					}
					mutatedH1 := append([]market.Candle(nil), data.h1...)
					for k := range mutatedH1 {
						if mutatedH1[k].Time+3600 > now {
							mutatedH1[k].High, mutatedH1[k].Low = 9999, 1
						}
					}
					if !reflect.DeepEqual(sessionlevel.DetectAsOf(data.cfg, sessionlevel.Input{M5: mutated, H1: mutatedH1}, i), live) {
						t.Errorf("%s candle %d: replacing the future with garbage changed the decision", file, i)
						return
					}
				}
				evaluations++
				episodes += len(live.Episodes)
			}
			t.Logf("%s: %d evaluations, %d episode observations", file, evaluations, episodes)
		}(c.File)
	}
	wg.Wait()
}

// TestSessionLevelSetupsOnRealCapturesSatisfyEveryTechnicalInvariant checks the
// contract on every setup real data produces, with checks written from the candles.
func TestSessionLevelSetupsOnRealCapturesSatisfyEveryTechnicalInvariant(t *testing.T) {
	doc := resolveDoc(t)
	total := 0
	for _, c := range captures {
		data := loadCapture(t, doc, c.File)
		pip := data.cfg.PipSize
		for i := range data.m5 {
			a := sessionlevel.DetectAsOf(data.cfg, sessionlevel.Input{M5: data.m5, H1: data.h1}, i)
			for _, s := range a.Setups {
				total++
				buy := s.Direction == market.Buy
				sign := 1.0
				if !buy {
					sign = -1
				}
				fail := func(format string, args ...any) {
					t.Helper()
					t.Fatalf("%s candle %d %s %s: %s", c.File, i, s.Direction, s.Level.Name, fmt.Sprintf(format, args...))
				}
				if !(sign*(s.EntryLow-s.Stop) > 0 && sign*(s.Target-s.EntryHigh) > 0) {
					fail("geometry not ordered %+v", s)
				}
				// A BUY reacts off a low (support), a SELL off a high (resistance).
				if (buy && s.Level.Side != sessionlevel.SideSupport) || (!buy && s.Level.Side != sessionlevel.SideResistance) {
					fail("direction and level side disagree")
				}
				if !(sign*(s.Stop-s.Level.Price) < 0) {
					fail("stop %.5f not beyond the level %.5f", s.Stop, s.Level.Price)
				}
				// The stop is beyond everything price traded to from the touch (or the
				// sweep) to the confirmation, the sweep wick included.
				for _, b := range data.m5 {
					if b.Time < s.Reaction.EpisodeAt-int64(data.cfg.ReclaimMaxBars+data.cfg.ConfirmationWindow)*300 || b.Time > s.ConfirmedAt {
						continue
					}
					if b.Time < s.Reaction.EpisodeAt {
						continue
					}
					extreme := b.Low
					if !buy {
						extreme = b.High
					}
					if !(sign*(s.Stop-extreme) < 0) {
						fail("stop %.5f is not beyond the reaction's extreme %.5f at %d", s.Stop, extreme, b.Time)
					}
				}
				if data.cfg.ExecutionStopMaxPips > 0 && s.RiskPips > data.cfg.ExecutionStopMaxPips+1e-9 {
					fail("risk %.1f pips beyond the envelope %.1f", s.RiskPips, data.cfg.ExecutionStopMaxPips)
				}
				if s.RewardRisk < data.cfg.MinimumRewardRisk-1e-9 || s.TargetRoomATR < data.cfg.MinimumTargetRoomATR-1e-9 {
					fail("reward/risk %.2f or room %.2f below the floor", s.RewardRisk, s.TargetRoomATR)
				}
				if !(s.Level.AvailableAt <= s.Reaction.EpisodeAt && s.Reaction.EpisodeAt <= s.ConfirmedAt && s.ConfirmedAt <= data.m5[i].Time) {
					fail("timeline not causal: %+v", s.Reaction)
				}
				if s.Level.AvailableAt != s.Level.PeriodEnd {
					fail("a level is available only when its period has ended: %+v", s.Level)
				}
				if age := (data.m5[i].Time - s.ConfirmedAt) / 300; age > int64(data.cfg.ConfirmationMaxAge) {
					fail("stale confirmation, age %d", age)
				}
				if s.Level.Coverage <= 0 || s.Level.Coverage > 1 {
					fail("coverage %v", s.Level.Coverage)
				}
				proximal := s.EntryHigh
				if !buy {
					proximal = s.EntryLow
				}
				if math.Abs(s.RiskPips-math.Abs(proximal-s.Stop)/pip) > 1e-6 {
					fail("risk bookkeeping")
				}
				if s.Reaction.Model == sessionlevel.ModelSweepReclaim && !(s.Reaction.SweepDepthATR > 0) {
					fail("a sweep without depth")
				}
				if s.TouchesKnown && s.PriorTouches > data.cfg.MaximumPriorTouches && s.Reaction.Model == sessionlevel.ModelFirstTouch {
					fail("a first touch on a level with %d earlier touches", s.PriorTouches)
				}
			}
		}
	}
	if total == 0 {
		t.Fatal("no setup was produced on the captures")
	}
	t.Logf("%d setup observations checked over %d captures", total, len(captures))
}

type goldenSetup struct {
	Direction   market.Direction `json:"direction"`
	Level       string           `json:"level"`
	LevelPrice  float64          `json:"level_price"`
	Model       string           `json:"model"`
	EpisodeAt   int64            `json:"episode_at"`
	ConfirmedAt int64            `json:"confirmed_at"`
	EntryLow    float64          `json:"entry_low"`
	EntryHigh   float64          `json:"entry_high"`
	Stop        float64          `json:"stop"`
	Target      float64          `json:"target"`
}

type goldenCapture struct {
	Capture  string         `json:"capture"`
	Symbol   string         `json:"symbol"`
	Levels   int            `json:"distinct_levels"`
	Setups   []goldenSetup  `json:"setups"`
	Episodes map[string]int `json:"episodes_by_outcome"`
}

// TestSessionLevelV4DetectGoldenOnRealCaptures pins the v4 technical decisions on the
// committed captures. The golden is generated by THIS contract (UPDATE_GOLDEN=1) and
// is separate from the frozen Python oracle goldens, which are never edited.
func TestSessionLevelV4DetectGoldenOnRealCaptures(t *testing.T) {
	doc := resolveDoc(t)
	var got []goldenCapture
	for _, c := range captures {
		data := loadCapture(t, doc, c.File)
		g := goldenCapture{Capture: c.File, Symbol: data.symbol, Episodes: map[string]int{}}
		seen, levels := map[string]bool{}, map[string]bool{}
		final := map[string]string{}
		for i := range data.m5 {
			a := sessionlevel.DetectAsOf(data.cfg, sessionlevel.Input{M5: data.m5, H1: data.h1}, i)
			for _, l := range a.Levels {
				levels[l.ID] = true
			}
			for _, s := range a.Setups {
				key := fmt.Sprintf("%s/%s/%s/%d/%d", s.Direction, s.Level.ID, s.Reaction.Model, s.Reaction.EpisodeAt, s.ConfirmedAt)
				if seen[key] {
					continue
				}
				seen[key] = true
				g.Setups = append(g.Setups, goldenSetup{
					Direction: s.Direction, Level: s.Level.ID, LevelPrice: round6(s.Level.Price), Model: s.Reaction.Model,
					EpisodeAt: s.Reaction.EpisodeAt, ConfirmedAt: s.ConfirmedAt,
					EntryLow: round6(s.EntryLow), EntryHigh: round6(s.EntryHigh), Stop: round6(s.Stop), Target: round6(s.Target),
				})
			}
			for _, ep := range a.Episodes {
				outcome := string(ep.State)
				if ep.Reason != "" {
					outcome += ":" + ep.Reason
				}
				if ep.State == sessionlevel.StateCandidate {
					outcome = "published"
				}
				if final[ep.Key()] != "published" {
					final[ep.Key()] = outcome
				}
			}
		}
		for _, outcome := range final {
			g.Episodes[outcome]++
		}
		g.Levels = len(levels)
		sort.Slice(g.Setups, func(i, j int) bool {
			if g.Setups[i].ConfirmedAt != g.Setups[j].ConfirmedAt {
				return g.Setups[i].ConfirmedAt < g.Setups[j].ConfirmedAt
			}
			return g.Setups[i].Level < g.Setups[j].Level
		})
		got = append(got, g)
	}
	path := filepath.Join("..", "..", "testdata", "session-level-v4-detect-golden.json")
	raw, err := json.MarshalIndent(got, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	raw = append(raw, '\n')
	if os.Getenv("UPDATE_GOLDEN") != "" {
		if err := os.WriteFile(path, raw, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("golden missing (UPDATE_GOLDEN=1 to create): %v", err)
	}
	if string(want) != string(raw) {
		t.Fatalf("Session Level v4 decisions drifted from the committed golden; if intended, regenerate with UPDATE_GOLDEN=1")
	}
}

func round6(v float64) float64 { return math.Round(v*1e6) / 1e6 }
