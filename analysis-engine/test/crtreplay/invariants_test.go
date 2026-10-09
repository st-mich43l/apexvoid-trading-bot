package crtreplay_test

import (
	"encoding/json"
	"fmt"
	"math"
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/crt"
)

type captureData struct {
	file, symbol string
	cfg          crt.Config
	m5, h1       []market.Candle
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
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Fatal(err)
	}
	for _, s := range settings.Strategies {
		if s.ID == crt.ID {
			cfg, err := crt.ParseConfig(s.Parameters)
			if err != nil {
				t.Fatal(err)
			}
			return captureData{file: file, symbol: string(capture.Symbol), cfg: cfg, m5: m5, h1: h1}
		}
	}
	t.Fatal("crt is not in the production catalog")
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

// closedH1 is what a live feed would have delivered by the close of M5 index i.
func closedH1(h1 []market.Candle, m5 []market.Candle, i int) []market.Candle {
	closeAt := m5[i].Time + 300
	n := sort.Search(len(h1), func(k int) bool { return h1[k].Time+3600 > closeAt })
	return h1[:n]
}

// firstEvaluable is the first M5 index scanned: every bar. Bars without enough
// history for an ATR simply produce no setup (insufficient_m5_history), exactly
// as in the engine, so offline scans see what the engine saw.
const firstEvaluable = 0

type found struct {
	asOf  int
	setup crt.Setup
}

// scanAll runs the detector at every M5 close of a capture, with the FUTURE
// present in the input, and returns every setup in order.
func scanAll(c captureData) (setups []found, rejections []crt.Rejection) {
	for i := firstEvaluable; i < len(c.m5); i++ {
		a := crt.DetectAsOf(c.cfg, crt.Input{H1: c.h1, M5: c.m5}, i)
		for _, s := range a.Setups {
			setups = append(setups, found{asOf: i, setup: s})
		}
		rejections = append(rejections, a.Rejections...)
	}
	return
}

// TestCRTHasNoFutureDependenceOnRealCaptures re-evaluates every M5 close of
// every capture twice: once with the whole capture (the future) present and
// asOf set to that bar, once with only the candles a live feed had by then.
// The decisions must be identical, bar for bar.
func TestCRTHasNoFutureDependenceOnRealCaptures(t *testing.T) {
	doc := resolveDoc(t)
	var wg sync.WaitGroup
	for _, c := range captures {
		wg.Add(1)
		go func(file string) {
			defer wg.Done()
			data := loadCapture(t, doc, file)
			for i := firstEvaluable; i < len(data.m5); i++ {
				withFuture := crt.DetectAsOf(data.cfg, crt.Input{H1: data.h1, M5: data.m5}, i)
				onlyPast := crt.Detect(data.cfg, crt.Input{H1: closedH1(data.h1, data.m5, i), M5: data.m5[:i+1]})
				if !reflect.DeepEqual(withFuture, onlyPast) {
					t.Errorf("%s: decision at M5 index %d changed when future candles were present:\n future %+v\n past   %+v", file, i, withFuture, onlyPast)
					return
				}
			}
		}(c.File)
	}
	wg.Wait()
}

// TestCRTSetupsOnRealCapturesSatisfyEveryTechnicalInvariant checks, against the
// raw candles, that every setup found over every capture is a real, causally
// ordered sweep -> reclaim -> structure shift with a stop beyond the actual
// manipulation extreme and the opposite H1 edge as objective.
func TestCRTSetupsOnRealCapturesSatisfyEveryTechnicalInvariant(t *testing.T) {
	doc := resolveDoc(t)
	total := 0
	for _, c := range captures {
		data := loadCapture(t, doc, c.File)
		setups, _ := scanAll(data)
		total += len(setups)
		byTime := func(bars []market.Candle, ts int64) (market.Candle, bool) {
			i := sort.Search(len(bars), func(k int) bool { return bars[k].Time >= ts })
			if i < len(bars) && bars[i].Time == ts {
				return bars[i], true
			}
			return market.Candle{}, false
		}
		for _, f := range setups {
			s := f.setup
			label := fmt.Sprintf("%s %s anchor %d confirmed %d", data.symbol, s.Direction, s.Anchor.OpenTime, s.ConfirmedAt)
			evalClose := data.m5[f.asOf].Time + 300
			sign := 1.0
			if s.Direction == market.Sell {
				sign = -1
			}
			// Causal order, all on closed candles.
			if !(s.Anchor.CloseTime <= s.Sweep.BarTime && s.Sweep.BarTime <= s.Reclaim.BarTime && s.Reclaim.BarTime <= s.ConfirmedAt && s.ConfirmedAt+300 <= evalClose) {
				t.Errorf("%s: time order violated: anchor close %d sweep %d reclaim %d confirmed %d eval close %d", label, s.Anchor.CloseTime, s.Sweep.BarTime, s.Reclaim.BarTime, s.ConfirmedAt, evalClose)
			}
			if s.Anchor.CloseTime > evalClose {
				t.Errorf("%s: anchor not closed at evaluation", label)
			}
			anchor, ok := byTime(data.h1, s.Anchor.OpenTime)
			if !ok || anchor.High != s.Anchor.High || anchor.Low != s.Anchor.Low {
				t.Errorf("%s: anchor does not match the H1 candle", label)
			}
			if s.Anchor.RangeATR < data.cfg.MinimumH1RangeATR {
				t.Errorf("%s: anchor range %.2f ATR below the minimum", label, s.Anchor.RangeATR)
			}
			// The sweep candle really pierced the swept edge by the threshold.
			sweep, _ := byTime(data.m5, s.Sweep.BarTime)
			swept, edge := sweep.Low, s.Anchor.Low
			if sign < 0 {
				swept, edge = sweep.High, s.Anchor.High
			}
			if !(sign*(edge-swept) > s.Sweep.Threshold) {
				t.Errorf("%s: sweep candle does not pierce the edge by the threshold", label)
			}
			// The stop is beyond the true manipulation extreme since the anchor closed.
			extreme := math.Inf(1) * sign
			for _, b := range data.m5 {
				if b.Time < s.Anchor.CloseTime || b.Time > s.ConfirmedAt {
					continue
				}
				if sign > 0 {
					extreme = math.Min(extreme, b.Low)
				} else {
					extreme = math.Max(extreme, b.High)
				}
			}
			if extreme != s.Sweep.ExtremePrice {
				t.Errorf("%s: reported extreme %v, candles say %v", label, s.Sweep.ExtremePrice, extreme)
			}
			if !(sign*(extreme-s.Stop) > 0) {
				t.Errorf("%s: stop %v is not beyond the sweep extreme %v", label, s.Stop, extreme)
			}
			// Reclaim is a close back inside the original range.
			reclaim, _ := byTime(data.m5, s.Reclaim.BarTime)
			if reclaim.Close < s.Anchor.Low || reclaim.Close > s.Anchor.High {
				t.Errorf("%s: reclaim close %v outside the anchor range", label, reclaim.Close)
			}
			// The structure shift closes beyond a swing that existed before it.
			if s.Shift == nil {
				t.Errorf("%s: no structure shift in the strict contract", label)
				continue
			}
			shiftBar, _ := byTime(data.m5, s.Shift.BarTime)
			pivot, ok := byTime(data.m5, s.Shift.LevelTime)
			level := pivot.High
			if sign < 0 {
				level = pivot.Low
			}
			// The swing precedes THIS episode's manipulation extreme (the deepest
			// point from the sweep candle to the reclaim), which may be later than
			// the anchor-scoped extreme the stop is placed beyond.
			episodeExtremeTime, episodeExtreme := int64(0), math.Inf(1)*sign
			for _, b := range data.m5 {
				if b.Time < s.Sweep.BarTime || b.Time > s.Reclaim.BarTime {
					continue
				}
				if (sign > 0 && b.Low < episodeExtreme) || (sign < 0 && b.High > episodeExtreme) {
					episodeExtremeTime = b.Time
					if sign > 0 {
						episodeExtreme = b.Low
					} else {
						episodeExtreme = b.High
					}
				}
			}
			if !ok || level != s.Shift.Level || s.Shift.LevelTime >= episodeExtremeTime || !(sign*(shiftBar.Close-level) > 0) {
				t.Errorf("%s: shift level %v / time %d is not a prior swing broken by the shift close %v", label, s.Shift.Level, s.Shift.LevelTime, shiftBar.Close)
			}
			// Objective is the opposite H1 edge and lies beyond the entry.
			wantTarget := s.Anchor.High
			if sign < 0 {
				wantTarget = s.Anchor.Low
			}
			proximal := s.EntryHigh
			if sign < 0 {
				proximal = s.EntryLow
			}
			if s.Target != wantTarget || !(sign*(s.Target-proximal) > 0) || !(sign*(proximal-s.Stop) > 0) {
				t.Errorf("%s: geometry stop %v entry [%v,%v] target %v", label, s.Stop, s.EntryLow, s.EntryHigh, s.Target)
			}
			if s.TechnicalRR < data.cfg.MinimumRewardRisk || (data.cfg.ExecutionStopMaxPips > 0 && s.RiskPips > data.cfg.ExecutionStopMaxPips) {
				t.Errorf("%s: rr %.2f risk %.1f pips breaks the configured gates", label, s.TechnicalRR, s.RiskPips)
			}
		}
	}
	if total < 5 {
		t.Fatalf("only %d setups across all captures: the invariants proved nothing", total)
	}
	t.Logf("%d setup observations satisfied every invariant", total)
}

type goldenSetup struct {
	Direction     string  `json:"direction"`
	AnchorTime    int64   `json:"anchor_time"`
	SweepTime     int64   `json:"sweep_time"`
	ConfirmedAt   int64   `json:"confirmed_at"`
	FirstObserved int64   `json:"first_observed_bar"`
	EntryLow      float64 `json:"entry_low"`
	EntryHigh     float64 `json:"entry_high"`
	Stop          float64 `json:"stop"`
	Target        float64 `json:"target"`
	RiskPips      float64 `json:"risk_pips"`
	RR            float64 `json:"technical_rr"`
}

type goldenCapture struct {
	Capture    string         `json:"capture"`
	Symbol     string         `json:"symbol"`
	Setups     []goldenSetup  `json:"setups"`
	Rejections map[string]int `json:"rejected_episodes_by_reason"`
}

func round(v float64, places int) float64 {
	p := math.Pow(10, float64(places))
	return math.Round(v*p) / p
}

// TestCRTV3DetectGoldenOnRealCaptures pins the CRT v3 technical decisions
// (before the confluence floor, a pure function of candles) on every committed
// capture. It is v3's OWN versioned fixture: the frozen Python oracle goldens
// are untouched. Regenerate deliberately with UPDATE_GOLDEN=1.
func TestCRTV3DetectGoldenOnRealCaptures(t *testing.T) {
	doc := resolveDoc(t)
	var got []goldenCapture
	for _, c := range captures {
		data := loadCapture(t, doc, c.File)
		g := goldenCapture{Capture: c.File, Symbol: data.symbol, Setups: []goldenSetup{}, Rejections: map[string]int{}}
		seen := map[[4]int64]bool{}
		seenRej := map[crt.Rejection]bool{}
		for i := firstEvaluable; i < len(data.m5); i++ {
			a := crt.DetectAsOf(data.cfg, crt.Input{H1: data.h1, M5: data.m5}, i)
			for _, s := range a.Setups {
				dir := int64(1)
				if s.Direction == market.Sell {
					dir = -1
				}
				key := [4]int64{dir, s.Anchor.OpenTime, s.Sweep.BarTime, s.ConfirmedAt}
				if seen[key] {
					continue
				}
				seen[key] = true
				g.Setups = append(g.Setups, goldenSetup{
					Direction: string(s.Direction), AnchorTime: s.Anchor.OpenTime, SweepTime: s.Sweep.BarTime, ConfirmedAt: s.ConfirmedAt,
					FirstObserved: data.m5[i].Time, EntryLow: round(s.EntryLow, 6), EntryHigh: round(s.EntryHigh, 6), Stop: round(s.Stop, 6),
					Target: round(s.Target, 6), RiskPips: round(s.RiskPips, 3), RR: round(s.TechnicalRR, 4),
				})
			}
			for _, r := range a.Rejections {
				if !seenRej[r] {
					seenRej[r] = true
					g.Rejections[r.Reason]++
				}
			}
		}
		got = append(got, g)
	}
	path := filepath.Join("..", "..", "testdata", "crt-v3-detect-golden.json")
	if os.Getenv("UPDATE_GOLDEN") != "" {
		raw, err := json.MarshalIndent(map[string]any{
			"description": "CRT v3 technical decisions (pre-confluence) on the committed real captures; regenerate with UPDATE_GOLDEN=1 go test ./test/crtreplay -run Golden",
			"captures":    got,
		}, "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("golden missing (generate with UPDATE_GOLDEN=1): %v", err)
	}
	var want struct {
		Captures []goldenCapture `json:"captures"`
	}
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, want.Captures) {
		gotRaw, _ := json.MarshalIndent(got, "", " ")
		t.Fatalf("CRT v3 decisions drifted from the committed golden; if intended, regenerate with UPDATE_GOLDEN=1.\n got: %s", gotRaw)
	}
}
