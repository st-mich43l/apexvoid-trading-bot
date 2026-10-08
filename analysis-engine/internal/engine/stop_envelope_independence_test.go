package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// TestEveryStrategyDeclaresItsOwnStopEnvelope: the stop envelope a strategy is
// admitted under is a per-strategy declaration. A new strategy that is not listed
// would silently take the trend default, so the catalog and the table must agree.
func TestEveryStrategyDeclaresItsOwnStopEnvelope(t *testing.T) {
	known := map[string]bool{}
	for _, id := range strategy.KnownIDs() {
		known[string(id)] = true
		if _, ok := strategyStopEnvelope[id]; !ok {
			t.Errorf("strategy %q declares no stop envelope kind", id)
		}
	}
	for id := range strategyStopEnvelope {
		if !known[string(id)] {
			t.Errorf("stop envelope table lists %q, which is not a catalog strategy", id)
		}
	}
	if len(known) != 21 {
		t.Fatalf("the catalog has %d strategies, want 21", len(known))
	}
}

// The values were a set of three strategy groups before; pinning them proves that
// declaring them per strategy changed no strategy's envelope.
func TestStopEnvelopeKindsAreUnchangedByDeclaringThemPerStrategy(t *testing.T) {
	want := map[string]stopEnvelopeKind{
		"key_level": stopEnvelopeReactionRoom, "session_level": stopEnvelopeReactionRoom, "trendline": stopEnvelopeReactionRoom,
		"range_sweep": stopEnvelopeM1Scalp, "impulse_pullback": stopEnvelopeM1Scalp, "scalp_breakout_retest": stopEnvelopeM1Scalp,
		"range_edge": stopEnvelopeRangeRoom, "fade_scalp": stopEnvelopeRangeRoom,
	}
	for _, id := range strategy.KnownIDs() {
		kind, special := want[string(id)]
		if !special {
			kind = stopEnvelopeTrend
		}
		if got := strategyStopEnvelope[id]; got != kind {
			t.Errorf("%s: stop envelope kind %d, want %d", id, got, kind)
		}
	}
}
