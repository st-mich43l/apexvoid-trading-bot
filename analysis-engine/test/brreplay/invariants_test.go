package brreplay_test

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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/breakretest"
)

type captureData struct {
	file, symbol string
	cfg          breakretest.Config
	m5           []market.Candle
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
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Fatal(err)
	}
	for _, s := range settings.Strategies {
		if s.ID == breakretest.ID {
			cfg, err := breakretest.ParseConfig(s.Parameters)
			if err != nil {
				t.Fatal(err)
			}
			return captureData{file: file, symbol: string(capture.Symbol), cfg: cfg, m5: m5}
		}
	}
	t.Fatal("break_retest is not in the production catalog")
	return captureData{}
}

func resolveDoc(t testing.TB) *config.Document {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

// TestBreakRetestHasNoFutureDependenceOnRealCaptures proves, bar by bar on every
// real capture, that the decision made with the future present equals the
// decision made with only the past, and that replacing the future with garbage
// changes nothing.
func TestBreakRetestHasNoFutureDependenceOnRealCaptures(t *testing.T) {
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
				asOf := breakretest.DetectAsOf(data.cfg, data.m5, i)
				live := breakretest.Detect(data.cfg, data.m5[:i+1])
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
					if !reflect.DeepEqual(breakretest.DetectAsOf(data.cfg, mutated, i), live) {
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

// TestBreakRetestSetupsOnRealCapturesSatisfyEveryTechnicalInvariant checks the
// contract on every setup real data produces.
func TestBreakRetestSetupsOnRealCapturesSatisfyEveryTechnicalInvariant(t *testing.T) {
	doc := resolveDoc(t)
	total := 0
	for _, c := range captures {
		data := loadCapture(t, doc, c.File)
		pip := data.cfg.PipSize
		for i := range data.m5 {
			for _, s := range breakretest.DetectAsOf(data.cfg, data.m5, i).Setups {
				total++
				buy := s.Direction == market.Buy
				sign := 1.0
				if !buy {
					sign = -1
				}
				fail := func(format string, args ...any) {
					t.Helper()
					t.Fatalf("%s candle %d %s: %s", c.File, i, s.Direction, fmt.Sprintf(format, args...))
				}
				if !(sign*(s.EntryLow-s.Stop) > 0 && sign*(s.Target-s.EntryHigh) > 0) {
					fail("geometry not ordered %+v", s)
				}
				for _, b := range data.m5 {
					if b.Time < s.Retest.TouchTime || b.Time > s.ConfirmedAt {
						continue
					}
					extreme := b.Low
					if !buy {
						extreme = b.High
					}
					if !(sign*(s.Stop-extreme) < 0) {
						fail("stop %.5f is not beyond the retest extreme %.5f", s.Stop, extreme)
					}
				}
				if !(sign*(s.Stop-s.ProtectedStructure) < 0) {
					fail("stop not beyond the protected structure")
				}
				if data.cfg.ExecutionStopMaxPips > 0 && s.RiskPips > data.cfg.ExecutionStopMaxPips+1e-9 {
					fail("risk %.1f pips beyond the envelope %.1f", s.RiskPips, data.cfg.ExecutionStopMaxPips)
				}
				if s.RewardRisk < data.cfg.MinimumRewardRisk-1e-9 {
					fail("reward/risk %.2f below the floor", s.RewardRisk)
				}
				if !(s.Reference.FormedAt < s.Break.StartTime && s.Break.StartTime <= s.Break.AcceptedAt && s.Break.AcceptedAt < s.Retest.TouchTime && s.Retest.TouchTime <= s.ConfirmedAt && s.ConfirmedAt <= data.m5[i].Time) {
					fail("timeline not causal: %+v", s)
				}
				if age := (data.m5[i].Time - s.ConfirmedAt) / 300; age > int64(data.cfg.ConfirmationMaxAgeBars) {
					fail("stale confirmation, age %d", age)
				}
				if s.Break.BodyRatio < data.cfg.MinBreakBodyRatio || s.Break.CloseStrength < data.cfg.MinBreakCloseStrength || s.Break.DisplacementATR < data.cfg.MinBreakDisplacement {
					fail("weak break %+v", s.Break)
				}
				if math.Abs(s.RiskPips-math.Abs(proximal(s)-s.Stop)/pip) > 1e-6 {
					fail("risk bookkeeping")
				}
			}
		}
	}
	t.Logf("%d setups checked over %d captures", total, len(captures))
}

func proximal(s breakretest.Setup) float64 {
	if s.Direction == market.Sell {
		return s.EntryLow
	}
	return s.EntryHigh
}

type goldenSetup struct {
	Direction   market.Direction `json:"direction"`
	Kind        string           `json:"kind"`
	ReferenceID string           `json:"reference_id"`
	BreakStart  int64            `json:"break_start"`
	AcceptedAt  int64            `json:"accepted_at"`
	TouchAt     int64            `json:"touch_at"`
	ConfirmedAt int64            `json:"confirmed_at"`
	EntryLow    float64          `json:"entry_low"`
	EntryHigh   float64          `json:"entry_high"`
	Stop        float64          `json:"stop"`
	Target      float64          `json:"target"`
}

type goldenCapture struct {
	Capture  string         `json:"capture"`
	Symbol   string         `json:"symbol"`
	Setups   []goldenSetup  `json:"setups"`
	Episodes map[string]int `json:"episodes_by_outcome"`
}

// TestBreakRetestV3DetectGoldenOnRealCaptures pins the v3 technical decisions
// on the committed captures. The golden is generated by THIS contract
// (UPDATE_GOLDEN=1) and is separate from the frozen Python oracle goldens, which
// are never edited.
func TestBreakRetestV3DetectGoldenOnRealCaptures(t *testing.T) {
	doc := resolveDoc(t)
	var got []goldenCapture
	for _, c := range captures {
		data := loadCapture(t, doc, c.File)
		g := goldenCapture{Capture: c.File, Symbol: data.symbol, Episodes: map[string]int{}}
		seen := map[string]bool{}
		final := map[string]string{}
		for i := range data.m5 {
			a := breakretest.DetectAsOf(data.cfg, data.m5, i)
			for _, s := range a.Setups {
				key := fmt.Sprintf("%s/%s/%d", s.Direction, s.Reference.ID, s.Break.StartTime)
				if seen[key] {
					continue
				}
				seen[key] = true
				g.Setups = append(g.Setups, goldenSetup{
					Direction: s.Direction, Kind: s.Reference.Kind, ReferenceID: s.Reference.ID, BreakStart: s.Break.StartTime, AcceptedAt: s.Break.AcceptedAt,
					TouchAt: s.Retest.TouchTime, ConfirmedAt: s.ConfirmedAt, EntryLow: round6(s.EntryLow), EntryHigh: round6(s.EntryHigh),
					Stop: round6(s.Stop), Target: round6(s.Target),
				})
			}
			for _, ep := range a.Episodes {
				outcome := string(ep.State)
				if ep.Reason != "" {
					outcome += ":" + ep.Reason
				}
				if ep.State == breakretest.StateCandidate {
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
		sort.Slice(g.Setups, func(i, j int) bool { return g.Setups[i].ConfirmedAt < g.Setups[j].ConfirmedAt })
		got = append(got, g)
	}
	path := filepath.Join("..", "..", "testdata", "break-retest-v3-detect-golden.json")
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
		t.Fatalf("Break & Retest v3 decisions drifted from the committed golden; if intended, regenerate with UPDATE_GOLDEN=1")
	}
}

func round6(v float64) float64 { return math.Round(v*1e6) / 1e6 }
