package techniquezone

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func closes(values ...float64) []market.Candle {
	bars := make([]market.Candle, len(values))
	for i, v := range values {
		bars[i] = bar(i, v, v+.3, v-.3, v)
	}
	return bars
}

func TestFindRetestBuyNeedsAnAcceptedBreakThenARetestThatHolds(t *testing.T) {
	bars := closes(98, 99, 101, 102, 103, 102.2, 100.1, 101)
	bars[6] = bar(6, 101, 101.2, 99.9, 100.4) // retests 100 and closes back above it
	zone, ok := FindRetest(bars, 100, 2, .1)
	if !ok || zone.Source != "retest_support" || zone.Side != "demand" || zone.OriginIndex != 6 {
		t.Fatalf("expected a support retest on bar 6, got %+v ok=%v", zone, ok)
	}
	if zone.Low() >= 100 || zone.High() <= 100 {
		t.Fatalf("the band must straddle the level: %+v", zone)
	}
	if _, ok := FindRetest(closes(98, 99, 101, 98, 99, 101.5, 99.5), 100, 2, .1); ok {
		t.Fatal("a single close beyond the level is not an accepted break")
	}
	// The retest must come after the break, never before it.
	before := closes(98, 100.2, 99.9, 101, 102, 103, 104)
	if _, ok := FindRetest(before, 100, 2, .1); ok {
		t.Fatal("a touch before the accepted break is not a retest")
	}
	failed := closes(98, 99, 101, 102, 103, 102.2, 99.2, 99.0)
	if _, ok := FindRetest(failed, 100, 2, .1); ok {
		t.Fatal("a retest that closes back below the level has not held")
	}
	if _, ok := FindRetest(closes(98, 99), 100, 1, .1); ok {
		t.Fatal("fewer than three bars cannot hold a retest")
	}
}

func TestFindRetestSellMirrorsBuy(t *testing.T) {
	bars := closes(104, 103, 99, 98, 97, 98, 99.8, 99)
	bars[6] = bar(6, 99, 100.1, 98.8, 99.6) // retests 100 from below and closes under it
	zone, ok := FindRetest(bars, 100, 2, .1)
	if !ok || zone.Source != "retest_resistance" || zone.Side != "supply" {
		t.Fatalf("expected a resistance retest, got %+v ok=%v", zone, ok)
	}
}

func TestCompatToleranceUsesTheFramePipFloor(t *testing.T) {
	bars := closes(100, 100.1, 100.05)
	if got := compatTolerance(bars, .1); got != .1 {
		t.Fatalf("a quiet frame falls back to the pip floor, got %v", got)
	}
	wide := closes(100, 140, 120)
	if got := compatTolerance(wide, .1); got < (140.3-99.7)*.003-1e-12 {
		t.Fatalf("a wide frame scales with its span, got %v", got)
	}
}

func TestEntryZoneFallsBackToABandAroundTheLevel(t *testing.T) {
	bars := closes(100, 100.1, 100.05, 100.08, 100.02, 100.06)
	zone := EntryZone(bars, 100.04, "BUY", .1, testCompat())
	tol := compatTolerance(bars, .1)
	if zone.Side != "demand" || zone.Low() != 100.04-tol || zone.High() != 100.04+tol {
		t.Fatalf("fallback band wrong: %+v tolerance %v", zone, tol)
	}
	if sell := EntryZone(bars, 100.04, "SELL", .1, testCompat()); sell.Side != "supply" {
		t.Fatalf("a sell entry is a supply band: %+v", sell)
	}
}

func testCompat() CompatConfig {
	return CompatConfig{PipSize: .1, DisplacementBodyFraction: .55, DisplacementATRMult: 1.5, FractalN: 2, ATRLength: 14, LevelClusterATR: .5, RoundStep: 5, MaximumClusterSpanMultiple: 2}
}
