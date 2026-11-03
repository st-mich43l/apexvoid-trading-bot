package aequality_test

import (
	"math"
	"path/filepath"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// TestKeyLevelForwardLabelsArePresentFiniteAndInRange: every published key_level candidate of the committed captures carries the six forward labels (docs/strategies/keylevel-edge-source.md §K1.5)
// as measurements, finite and in range. They are never gates or stars; TestNoStrategyAssertsAConstantAsAMeasurement keeps them from being constants.
func TestKeyLevelForwardLabelsArePresentFiniteAndInRange(t *testing.T) {
	seen := 0
	kinds := map[float64]int{}
	for _, r := range replayAll(t) {
		if r.Strategy != "key_level" {
			continue
		}
		seen++
		get := func(name string) float64 {
			v, ok := r.Components[name]
			if !ok || math.IsNaN(v) || math.IsInf(v, 0) {
				t.Errorf("%s: key_level candidate %s lacks the finite measurement %s (%v)", r.Capture, r.ID, name, v)
			}
			return v
		}
		kind, class, origin := get("level_kind"), get("level_round_class"), get("level_origin")
		age, band, coverage := get("level_age_bars"), get("level_band_atr"), get("level_coverage")
		kinds[kind]++
		switch {
		case kind != 0 && kind != 1:
			t.Errorf("%s: level_kind %v", r.ID, kind)
		case class < 0 || class > 4 || class != math.Trunc(class):
			t.Errorf("%s: level_round_class %v", r.ID, class)
		case origin != -1 && origin != 0 && origin != 1:
			t.Errorf("%s: level_origin %v", r.ID, origin)
		case age < 0 || age > 2000:
			t.Errorf("%s: level_age_bars %v", r.ID, age)
		case !(band > 0 && band < 10):
			t.Errorf("%s: level_band_atr %v", r.ID, band)
		case coverage < 0 || coverage > 1:
			t.Errorf("%s: level_coverage %v", r.ID, coverage)
		}
		for name := range r.Components {
			if len(name) > 6 && name[:6] == "score_" && (name == "score_level_kind" || name == "score_level_origin" || name == "score_level_coverage" || name == "score_level_round_class") {
				t.Errorf("%s: %s is a scored dimension; the labels are measurements only", r.ID, name)
			}
		}
	}
	if seen < 20 {
		t.Fatalf("only %d key_level candidates replayed", seen)
	}
	t.Logf("%d key_level candidates carry the labels; level_kind counts %v", seen, kinds)
}

// TestKeyLevelVariantBuildersAreDeterministicAndStayInsideTheFrame probes the variant level sources of the edge measurement on a real replay: every call must be repeatable, depend on the seed only
// where it should (C), and use nothing but the frame the engine hands it (levels inside the window's range, swings already confirmed at the evaluated bar), so no variant can look ahead.
func TestKeyLevelVariantBuildersAreDeterministicAndStayInsideTheFrame(t *testing.T) {
	doc, err := config.ResolveDocument(configPath())
	if err != nil {
		t.Fatal(err)
	}
	loaded, err := replaycapture.Load(filepath.Join("..", "..", "testdata", "replay-xau-production-capture-20260921.json"))
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	calls, differentSeeds := 0, 0
	probe := func(frame *analysiscontext.LegacyFrame, barTime int64) []techniquezone.Level {
		mu.Lock()
		defer mu.Unlock()
		calls++
		last := len(frame.Bars) - 1
		if frame.Bars[last].Time != barTime {
			t.Errorf("seed %d is not the latest closed bar %d", barTime, frame.Bars[last].Time)
		}
		for _, s := range frame.Swings {
			if s.Index > last-2 {
				t.Errorf("a swing at %d is unconfirmed at bar %d (fractal_n 2)", s.Index, last)
			}
		}
		low, high := frame.Bars[0].Low, frame.Bars[0].High
		for _, b := range frame.Bars {
			low, high = math.Min(low, b.Low), math.Max(high, b.High)
		}
		for name, build := range map[string]func(*analysiscontext.LegacyFrame, int64) []techniquezone.Level{
			"B": swingAreaLevels, "C1": randomLevels("probe", 1), "C2": randomLevels("probe", 2), "D": v7Levels,
		} {
			first, again := build(frame, barTime), build(frame, barTime)
			if len(first) != len(again) {
				t.Fatalf("%s: not repeatable (%d vs %d levels)", name, len(first), len(again))
			}
			for i := range first {
				if first[i] != again[i] {
					t.Fatalf("%s: level %d differs between two calls on the same frame", name, i)
				}
				if first[i].Price < low-1e-9 && name != "D" || first[i].Price > high+1e-9 && name != "D" {
					t.Errorf("%s: level %.2f outside the window's range [%.2f, %.2f]", name, first[i].Price, low, high)
				}
			}
		}
		if len(frame.SwingLevels) > 0 {
			a, b := randomLevels("probe", 1)(frame, barTime), randomLevels("probe", 2)(frame, barTime)
			if len(a) != len(frame.SwingLevels) || len(b) != len(frame.SwingLevels) {
				t.Errorf("C draws %d / %d levels, A has %d", len(a), len(b), len(frame.SwingLevels))
			}
			if len(a) > 0 && a[0].Price != b[0].Price {
				differentSeeds++
			}
		}
		return frame.SwingLevels // the live levels: the probe changes nothing
	}
	if _, err := replaycapture.Replay(doc, loaded, market.M5, replaycapture.Options{KeyLevelSource: probe}); err != nil {
		t.Fatal(err)
	}
	if calls < 200 || differentSeeds < calls/4 {
		t.Errorf("probe called %d times, seeds differed on %d", calls, differentSeeds)
	}
}
