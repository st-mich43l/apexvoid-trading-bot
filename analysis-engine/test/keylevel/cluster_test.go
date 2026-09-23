package keylevel_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func keyLevelTestConfig() keylevel.Config {
	return keylevel.Config{ClusterATR: 0.5, RoundStep: 5.0, MinimumTouches: 2, MaximumClusterSpanMultiple: 2.0}
}

func kSwing(kind structure.PivotKind, price float64) structure.Swing {
	return structure.Swing{Kind: kind, Price: market.Price(price)}
}

func levelsByKind(levels []keylevel.Level, kind keylevel.Kind) []keylevel.Level {
	var out []keylevel.Level
	for _, l := range levels {
		if l.Kind == kind {
			out = append(out, l)
		}
	}
	return out
}

// TestClusterGroupsNearbySwingsIntoOneReactionLevel ports key_levels()'s
// core clustering rule: atr=10, ClusterATR=0.5 -> tolerance=5. Three
// swings all within 5 of each other join one cluster and average.
// RoundStep is widened so no round-number level lands near enough to
// merge into this cluster and shift its price — isolating the pure
// clustering behavior this test targets (round+reaction interaction has
// its own dedicated test below).
func TestClusterGroupsNearbySwingsIntoOneReactionLevel(t *testing.T) {
	cfg := keyLevelTestConfig()
	cfg.RoundStep = 1000
	swings := []structure.Swing{
		kSwing(structure.SwingHigh, 100),
		kSwing(structure.SwingLow, 102),
		kSwing(structure.SwingHigh, 98),
	}
	levels := keylevel.Cluster(nil, 10, swings, cfg)

	reaction := levelsByKind(levels, keylevel.KindReaction)
	if len(reaction) != 1 {
		t.Fatalf("expected exactly one reaction cluster, got %d: %+v", len(reaction), reaction)
	}
	if reaction[0].Touches != 3 {
		t.Errorf("Touches = %d, want 3", reaction[0].Touches)
	}
	if float64(reaction[0].Price) != 100 { // (100+102+98)/3 = 100
		t.Errorf("Price = %v, want 100 (average)", reaction[0].Price)
	}
}

// TestClusterDropsClustersBelowMinimumTouches: a lone swing (touches=1)
// must not produce a level when MinimumTouches=2.
func TestClusterDropsClustersBelowMinimumTouches(t *testing.T) {
	swings := []structure.Swing{kSwing(structure.SwingHigh, 100)}
	levels := keylevel.Cluster(nil, 10, swings, keyLevelTestConfig())
	if len(levelsByKind(levels, keylevel.KindReaction)) != 0 {
		t.Fatalf("a single swing must not clear MinimumTouches=2, got %+v", levels)
	}
}

// TestClusterCapsSpanEvenWhenEveryPairwiseGapClears ports
// _can_join_cluster's dual gate: a chain of swings each individually
// within tolerance of its neighbor can still be rejected once the
// CLUSTER'S TOTAL SPAN exceeds tolerance*MaximumClusterSpanMultiple.
func TestClusterCapsSpanEvenWhenEveryPairwiseGapClears(t *testing.T) {
	cfg := keyLevelTestConfig() // atr=10 -> tolerance=5, maxSpan=10
	// Chain: 100, 104, 108, 112 — each adjacent pair differs by 4 (<=5,
	// so each new swing is within `tolerance` of the cluster's existing
	// members individually), but 100 and 112 differ by 12 (> maxSpan=10
	// AND > tolerance=5), so 112 must NOT join.
	swings := []structure.Swing{
		kSwing(structure.SwingHigh, 100),
		kSwing(structure.SwingLow, 104),
		kSwing(structure.SwingHigh, 108),
		kSwing(structure.SwingLow, 112),
	}
	levels := keylevel.Cluster(nil, 10, swings, cfg)
	if len(levels) == 0 {
		t.Fatal("expected at least one level")
	}
	// Checked across every level regardless of Kind: dedupe's merge pass
	// can reassign an entry's Kind (see dedupe's own doc comment), so a
	// Reaction-only filter here would not robustly catch a same-cluster
	// violation that happened to end up labeled Round after merging.
	for _, l := range levels {
		if l.Touches == 4 {
			t.Fatalf("all 4 swings joined one level despite spanning 12 > maxSpan=10: %+v", l)
		}
	}
}

// TestClusterRoundLevelsScanTheFullRoundStepGrid ports _round_levels: a
// round-number price with enough nearby swings is reported even without
// any swing sitting exactly on it.
func TestClusterRoundLevelsScanTheFullRoundStepGrid(t *testing.T) {
	cfg := keyLevelTestConfig() // RoundStep=5, atr=10 -> tolerance=5, round band=max(tolerance, atr*0.25)=max(5,2.5)=5
	swings := []structure.Swing{
		kSwing(structure.SwingHigh, 97),  // within 5 of round level 100 (and 95)
		kSwing(structure.SwingLow, 103),  // within 5 of round level 100 (and 105)
		kSwing(structure.SwingHigh, 200), // far away, keeps the round grid wide but isolated
	}
	levels := keylevel.Cluster(nil, 10, swings, cfg)
	round := levelsByKind(levels, keylevel.KindRound)
	found := false
	for _, l := range round {
		if float64(l.Price) == 100 {
			found = true
			if l.Touches < 2 {
				t.Errorf("round level 100 should count both nearby swings, got touches=%d", l.Touches)
			}
		}
	}
	if !found {
		t.Fatalf("expected a round level at 100, got %+v", round)
	}
}

// TestClusterDedupesAdjacentLevelsByPrice verifies the merge pass: a
// reaction cluster and a round level that land at the same price collapse
// into one. RoundStep is widened to 100 so exactly one round price (100)
// falls near the swings — avoiding a multi-level merge cascade that would
// make the expected result order-sensitive.
func TestClusterDedupesAdjacentLevelsByPrice(t *testing.T) {
	cfg := keyLevelTestConfig()
	cfg.RoundStep = 100
	swings := []structure.Swing{
		kSwing(structure.SwingHigh, 99),
		kSwing(structure.SwingLow, 101),
	} // reaction cluster @100 (touches=2); round level @100 only (touches=2) — round@0 is too far to qualify
	levels := keylevel.Cluster(nil, 10, swings, cfg)
	if len(levels) != 1 {
		t.Fatalf("expected the reaction cluster and round level at 100 to merge into exactly one entry, got %d: %+v", len(levels), levels)
	}
	if levels[0].Price != 100 || levels[0].Touches != 2 {
		t.Errorf("merged level = %+v, want price=100 touches=2", levels[0])
	}
}

func absDelta(a, b float64) float64 {
	if a > b {
		return a - b
	}
	return b - a
}

// TestClusterWickTouchReEnrichmentUsesRealOHLCOverlap ports
// _with_wick_touches: a level's touches count is replaced by the real
// wick-episode count when candles are supplied and that count is higher
// than the fractal-swing count.
func TestClusterWickTouchReEnrichmentUsesRealOHLCOverlap(t *testing.T) {
	cfg := keyLevelTestConfig() // atr=10 -> tolerance=5
	swings := []structure.Swing{
		kSwing(structure.SwingHigh, 99),
		kSwing(structure.SwingLow, 101),
	} // touches=2 from clustering alone
	// Candles: 3 separate episodes touching the [95,105] band around 100
	// (tolerance=5), each separated by a bar that closes decisively away
	// — more than the 2 fractal touches.
	candles := []market.Candle{
		{Time: 1, Open: 90, High: 102, Low: 89, Close: 90}, // episode 1 (touches band, closes back below)
		{Time: 2, Open: 80, High: 82, Low: 78, Close: 80},  // outside band, breaks the episode
		{Time: 3, Open: 90, High: 103, Low: 89, Close: 90}, // episode 2
		{Time: 4, Open: 80, High: 82, Low: 78, Close: 80},  // outside band again
		{Time: 5, Open: 90, High: 104, Low: 89, Close: 90}, // episode 3
	}
	levels := keylevel.Cluster(candles, 10, swings, cfg)
	if len(levels) == 0 {
		t.Fatal("expected at least one level")
	}
	var target *keylevel.Level
	for i := range levels {
		if absDelta(float64(levels[i].Price), 100) <= 5 {
			target = &levels[i]
		}
	}
	if target == nil {
		t.Fatalf("expected a level near 100, got %+v", levels)
	}
	if target.Touches != 3 {
		t.Errorf("expected wick-touch re-enrichment to raise Touches to 3 (real episode count), got %d", target.Touches)
	}
}

func TestClusterReturnsNilForNoSwings(t *testing.T) {
	if levels := keylevel.Cluster(nil, 10, nil, keyLevelTestConfig()); levels != nil {
		t.Errorf("expected nil for no swings, got %+v", levels)
	}
}

func TestUpdateUsesTheLastATRSeriesValue(t *testing.T) {
	swings := []structure.Swing{
		kSwing(structure.SwingHigh, 99),
		kSwing(structure.SwingLow, 101),
		kSwing(structure.SwingHigh, 100),
	}
	candles := []market.Candle{{Time: 1, Open: 100, High: 101, Low: 99, Close: 100}}
	state := keylevel.Update(candles, []float64{1, 1, 10}, swings, keyLevelTestConfig())
	if len(state.Levels) == 0 {
		t.Fatal("expected at least one level using the last ATR value (10)")
	}
}
