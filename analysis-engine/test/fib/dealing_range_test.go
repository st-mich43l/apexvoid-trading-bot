package fib_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func fibTestConfig() fib.Config {
	return fib.Config{EpsilonATR: 0.15, DeepDiscount: 0.382, DeepPremium: 0.618, EqHalfBand: 0.05}
}

func swing(kind structure.PivotKind, price float64) structure.Swing {
	return structure.Swing{Kind: kind, Price: market.Price(price)}
}

// TestResolvePrefersABracketingOlderPairOverTheMostRecentSwing ports
// _bracketing_pair's own priority over _last_opposing_pair: even though
// the most recent swing's nearest opposite doesn't contain price, an
// older opposing pair that DOES contain it must still win.
func TestResolvePrefersABracketingOlderPairOverTheMostRecentSwing(t *testing.T) {
	swings := []structure.Swing{
		swing(structure.SwingLow, 10),  // idx0
		swing(structure.SwingHigh, 20), // idx1 — pairs with idx0 as [10,20], brackets price=15
		swing(structure.SwingLow, 25),  // idx2 — most recent; its nearest opposite (idx1) gives [20,25], does NOT bracket 15
	}
	dr, ok := fib.Resolve(swings, 15, fibTestConfig())
	if !ok {
		t.Fatal("expected a resolved dealing range")
	}
	if dr.Low != 10 || dr.High != 20 {
		t.Fatalf("expected the bracketing [10,20] pair, got [%v,%v] — the most-recent-swing-only pair [20,25] must not win when an older pair actually contains price", dr.Low, dr.High)
	}
}

// TestResolveFallsBackToLastOpposingPairWithoutContainmentCheck ports
// _last_opposing_pair's own contract: unlike _bracketing_pair, it never
// checks whether price falls inside the pair it returns.
func TestResolveFallsBackToLastOpposingPairWithoutContainmentCheck(t *testing.T) {
	swings := []structure.Swing{
		swing(structure.SwingLow, 10),
		swing(structure.SwingHigh, 20),
	}
	dr, ok := fib.Resolve(swings, 1000, fibTestConfig()) // far outside [10,20]
	if !ok {
		t.Fatal("expected a fallback dealing range even though price is outside every swing pair")
	}
	if dr.Low != 10 || dr.High != 20 {
		t.Fatalf("expected the fallback pair [10,20], got [%v,%v]", dr.Low, dr.High)
	}
	if dr.Position != 1.0 {
		t.Errorf("Position should clamp to 1.0 for a price far past High, got %v", dr.Position)
	}
}

func TestResolveFailsWithoutAnyOpposingSwingPair(t *testing.T) {
	cfg := fibTestConfig()
	if _, ok := fib.Resolve([]structure.Swing{swing(structure.SwingLow, 10)}, 15, cfg); ok {
		t.Error("a single swing cannot form a pair")
	}
	sameKind := []structure.Swing{
		swing(structure.SwingLow, 10), swing(structure.SwingLow, 20), swing(structure.SwingLow, 30),
	}
	if _, ok := fib.Resolve(sameKind, 15, cfg); ok {
		t.Error("swings all of the same kind can never form an opposing pair")
	}
}

// TestResolveZoneAndFibZoneLabels ports dealing_range()/fib_zone_label's
// exact threshold table against a controlled [0,100] bracketing pair.
func TestResolveZoneAndFibZoneLabels(t *testing.T) {
	swings := []structure.Swing{swing(structure.SwingLow, 0), swing(structure.SwingHigh, 100)}
	cfg := fibTestConfig()

	cases := []struct {
		position    float64
		wantZone    string
		wantFibZone string
	}{
		{0.50, "eq", "eq"},
		{0.47, "eq", "eq"}, // within the 0.05 half-band of 0.5
		{0.30, "discount", "deep_discount"},
		{0.40, "discount", "discount"},
		{0.55, "premium", "premium"},
		{0.70, "premium", "deep_premium"},
	}
	for _, tc := range cases {
		dr, ok := fib.Resolve(swings, market.Price(tc.position*100), cfg)
		if !ok {
			t.Fatalf("position %v: expected a resolved range", tc.position)
		}
		if dr.Zone != tc.wantZone {
			t.Errorf("position %v: Zone = %q, want %q", tc.position, dr.Zone, tc.wantZone)
		}
		if dr.FibZone != tc.wantFibZone {
			t.Errorf("position %v: FibZone = %q, want %q", tc.position, dr.FibZone, tc.wantFibZone)
		}
	}
}

// TestUpdateBuildsLadderAndRangeFromTheSameSwingPair verifies doc.go's
// own central invariant: the ladder and the dealing range are built from
// one shared bracketing search, not two independent ones.
func TestUpdateBuildsLadderAndRangeFromTheSameSwingPair(t *testing.T) {
	candles := []market.Candle{
		{Time: 1, Open: 15, High: 16, Low: 14, Close: 15}, // last close = 15, inside [10,20]
	}
	swings := []structure.Swing{
		swing(structure.SwingLow, 10),
		swing(structure.SwingHigh, 20),
	}
	state := fib.Update(candles, swings, fibTestConfig())

	if state.Range == nil {
		t.Fatal("expected a resolved Range")
	}
	if state.Range.Low != 10 || state.Range.High != 20 {
		t.Fatalf("Range = %+v, want Low=10 High=20", state.Range)
	}
	var ext1 fib.Level
	found := false
	for _, l := range state.Ladder {
		if l.Kind == fib.KindExtension && l.Ratio == 1.0 {
			ext1 = l
			found = true
		}
	}
	if !found {
		t.Fatal("expected a ratio=1.0 extension level")
	}
	if float64(ext1.Price) != float64(state.Range.High) {
		t.Errorf("the ratio=1.0 extension (%v) must equal Range.High (%v) — same bracketing pair", ext1.Price, state.Range.High)
	}
}

func TestUpdateReturnsEmptyStateWithoutCandlesOrSwings(t *testing.T) {
	cfg := fibTestConfig()
	if state := fib.Update(nil, nil, cfg); state.Range != nil || state.Ladder != nil {
		t.Fatalf("no candles: expected zero State, got %+v", state)
	}
	candles := []market.Candle{{Time: 1, Open: 15, High: 16, Low: 14, Close: 15}}
	if state := fib.Update(candles, nil, cfg); state.Range != nil || state.Ladder != nil {
		t.Fatalf("no swings: expected zero State, got %+v", state)
	}
}
