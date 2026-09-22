package structure_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func TestClassifyEvent_UnheldBreaksAreNeverAnEvent(t *testing.T) {
	before := structure.LayerState{Trend: structure.TrendBullish}
	for _, bt := range []structure.BreakType{structure.BreakWick, structure.BreakSweep, structure.BreakFailed} {
		brk := structure.StructureBreak{Type: bt, Direction: market.Buy}
		if got := structure.ClassifyEvent(brk, before); got != structure.EventNone {
			t.Errorf("%v must never be an event, got %v", bt, got)
		}
	}
}

func TestClassifyEvent_ContinuationIsBOS(t *testing.T) {
	before := structure.LayerState{Trend: structure.TrendBullish}
	brk := structure.StructureBreak{Type: structure.BreakClose, Direction: market.Buy}
	if got := structure.ClassifyEvent(brk, before); got != structure.EventBOS {
		t.Errorf("bullish trend + close break upward: expected BOS, got %v", got)
	}

	beforeBear := structure.LayerState{Trend: structure.TrendBearish}
	brkDown := structure.StructureBreak{Type: structure.BreakDisplacement, Direction: market.Sell}
	if got := structure.ClassifyEvent(brkDown, beforeBear); got != structure.EventBOS {
		t.Errorf("bearish trend + displacement break downward: expected BOS, got %v", got)
	}
}

func TestClassifyEvent_BreakingTheProtectedLevelCounterTrendIsCHoCH(t *testing.T) {
	protectedLow := structure.Swing{ID: "low-1"}
	before := structure.LayerState{Trend: structure.TrendBullish, ProtectedLow: &protectedLow}
	brk := structure.StructureBreak{Type: structure.BreakClose, Direction: market.Sell, BrokenSwingID: "low-1"}
	if got := structure.ClassifyEvent(brk, before); got != structure.EventCHoCH {
		t.Errorf("close break of the protected low while bullish: expected CHoCH, got %v", got)
	}

	protectedHigh := structure.Swing{ID: "high-1"}
	beforeBear := structure.LayerState{Trend: structure.TrendBearish, ProtectedHigh: &protectedHigh}
	brkUp := structure.StructureBreak{Type: structure.BreakDisplacement, Direction: market.Buy, BrokenSwingID: "high-1"}
	if got := structure.ClassifyEvent(brkUp, beforeBear); got != structure.EventCHoCH {
		t.Errorf("displacement break of the protected high while bearish: expected CHoCH, got %v", got)
	}
}

func TestClassifyEvent_CounterTrendBreakOfANonProtectedSwingIsNeither(t *testing.T) {
	protectedLow := structure.Swing{ID: "low-1"}
	before := structure.LayerState{Trend: structure.TrendBullish, ProtectedLow: &protectedLow}
	// Breaks DOWN through some other, non-protected internal low — a
	// pullback, not yet evidence the bullish read at THIS layer is wrong.
	brk := structure.StructureBreak{Type: structure.BreakClose, Direction: market.Sell, BrokenSwingID: "some-other-low"}
	if got := structure.ClassifyEvent(brk, before); got != structure.EventNone {
		t.Errorf("expected EventNone (not the protected level), got %v", got)
	}
}

func TestClassifyEvent_FirstDirectionalBreakFromRangeIsAnInitiatingBOS(t *testing.T) {
	before := structure.LayerState{Trend: structure.TrendRange}
	brk := structure.StructureBreak{Type: structure.BreakClose, Direction: market.Buy}
	if got := structure.ClassifyEvent(brk, before); got != structure.EventBOS {
		t.Errorf("first directional close from Range: expected an initiating BOS, got %v", got)
	}

	beforeUnknown := structure.LayerState{Trend: structure.TrendUnknown}
	brkDown := structure.StructureBreak{Type: structure.BreakClose, Direction: market.Sell}
	if got := structure.ClassifyEvent(brkDown, beforeUnknown); got != structure.EventBOS {
		t.Errorf("first directional close from Unknown: expected an initiating BOS, got %v", got)
	}
}
