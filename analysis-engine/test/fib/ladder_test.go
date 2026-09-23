package fib_test

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
)

func almostEqual(a, b float64) bool {
	return math.Abs(a-b) < 1e-9
}

// TestLadderRetracementsAndExtensions ports fibonacci.py's own exact
// ratio table and anchor convention: retracements measured back down
// from high, extensions projected up from low.
func TestLadderRetracementsAndExtensions(t *testing.T) {
	levels := fib.Ladder(100, 200, true)
	if len(levels) != 8 {
		t.Fatalf("expected 5 retracement + 3 extension levels, got %d: %+v", len(levels), levels)
	}

	want := map[float64]float64{
		0.236: 200 - 0.236*100,
		0.382: 200 - 0.382*100,
		0.5:   150,
		0.618: 200 - 0.618*100,
		0.786: 200 - 0.786*100,
	}
	for _, l := range levels {
		if l.Kind != fib.KindRetracement {
			continue
		}
		expected, ok := want[l.Ratio]
		if !ok {
			t.Fatalf("unexpected retracement ratio %v", l.Ratio)
		}
		if !almostEqual(float64(l.Price), expected) {
			t.Errorf("retracement %v: price = %v, want %v", l.Ratio, l.Price, expected)
		}
	}

	extWant := map[float64]float64{
		1.0:   200,
		1.272: 100 + 1.272*100,
		1.618: 100 + 1.618*100,
	}
	for _, l := range levels {
		if l.Kind != fib.KindExtension {
			continue
		}
		expected, ok := extWant[l.Ratio]
		if !ok {
			t.Fatalf("unexpected extension ratio %v", l.Ratio)
		}
		if !almostEqual(float64(l.Price), expected) {
			t.Errorf("extension %v: price = %v, want %v", l.Ratio, l.Price, expected)
		}
	}
}

func TestLadderExcludesExtensionsWhenNotRequested(t *testing.T) {
	levels := fib.Ladder(100, 200, false)
	if len(levels) != 5 {
		t.Fatalf("expected only 5 retracement levels, got %d: %+v", len(levels), levels)
	}
	for _, l := range levels {
		if l.Kind == fib.KindExtension {
			t.Fatalf("extension level present despite includeExtensions=false: %+v", l)
		}
	}
}

func TestLadderReturnsNilForZeroOrInvertedSpan(t *testing.T) {
	if levels := fib.Ladder(200, 200, true); levels != nil {
		t.Errorf("zero span: want nil, got %+v", levels)
	}
	if levels := fib.Ladder(200, 100, true); levels != nil {
		t.Errorf("inverted span (high < low): want nil, got %+v", levels)
	}
}

// TestNearestLevelDefaultsToRetracementOnly ports nearest_fib's own
// kinds=("retracement",) default — an extension within the same band
// must not be reported when no kinds are explicitly requested.
func TestNearestLevelDefaultsToRetracementOnly(t *testing.T) {
	levels := fib.Ladder(100, 200, true) // retracement 0.5 -> 150; extension 1.0 -> 200
	// atr=10, epsilon=0.15 -> band=1.5; price 200.5 is within 1.5 of the
	// extension (200) but far from every retracement.
	_, ok := fib.NearestLevel(levels, 200.5, 10, 0.15)
	if ok {
		t.Fatal("no retracement level is within band of 200.5 — the extension-only match must not be returned when kinds is omitted")
	}

	level, ok := fib.NearestLevel(levels, 200.5, 10, 0.15, fib.KindExtension)
	if !ok || level.Ratio != 1.0 {
		t.Fatalf("expected the ratio=1.0 extension when KindExtension is explicitly requested, got %+v (ok=%v)", level, ok)
	}
}

func TestNearestLevelPicksTheClosestWithinBand(t *testing.T) {
	levels := fib.Ladder(0, 100, false) // retracements at 21.4, 61.8, 50, 38.2, 21.4 (0.236/0.382/0.5/0.618/0.786)
	// price=51, atr=10, epsilon=0.15 -> band=1.5: only the 0.5 level (50) qualifies.
	level, ok := fib.NearestLevel(levels, 51, 10, 0.15)
	if !ok || level.Ratio != 0.5 {
		t.Fatalf("expected the 0.5 retracement (50) nearest to 51, got %+v (ok=%v)", level, ok)
	}
}

func TestNearestLevelRejectsWhenNothingWithinBand(t *testing.T) {
	levels := fib.Ladder(0, 100, false)
	if _, ok := fib.NearestLevel(levels, 1000, 10, 0.15); ok {
		t.Fatal("no level should qualify at a price far outside every level's band")
	}
}
