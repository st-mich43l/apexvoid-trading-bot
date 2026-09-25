package structure_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// swingAt builds a Swing whose referenceATR (ExcursionPrice/ExcursionATR)
// is exactly 5 — matching structure.go's own recovery of a swing's
// original ATR scale — so ClassifySwingRelation's tolerance band inside
// BuildLayerState is easy to reason about by hand (tol=0.05 -> band=0.25).
func swingAt(kind structure.SwingKind, price float64, confirmedAt int64) structure.Swing {
	return structure.Swing{
		Kind: kind, Layer: structure.StructureMicro, Price: market.Price(price),
		ExcursionPrice: 5, ExcursionATR: 1, ConfirmedAt: confirmedAt,
	}
}

func TestBuildLayerState_HHPlusHLEstablishesBullishAndProtectedLow(t *testing.T) {
	swings := []structure.Swing{
		swingAt(structure.SwingLow, 100, 1),
		swingAt(structure.SwingHigh, 110, 2),
		swingAt(structure.SwingLow, 105, 3),  // HigherLow vs the first low
		swingAt(structure.SwingHigh, 115, 4), // HigherHigh vs the first high -> now HH+HL -> Bullish
	}
	state := structure.BuildLayerState(structure.StructureMicro, swings, 0.05)

	if state.Trend != structure.TrendBullish {
		t.Fatalf("expected TrendBullish, got %v", state.Trend)
	}
	if state.ProtectedLow == nil || float64(state.ProtectedLow.Price) != 105 {
		t.Errorf("expected ProtectedLow at 105 (the established higher low), got %+v", state.ProtectedLow)
	}
	if state.LastHigh == nil || float64(state.LastHigh.Price) != 115 {
		t.Errorf("expected LastHigh 115, got %+v", state.LastHigh)
	}
}

func TestBuildLayerState_LHPlusLLEstablishesBearishAndProtectedHigh(t *testing.T) {
	swings := []structure.Swing{
		swingAt(structure.SwingHigh, 100, 1),
		swingAt(structure.SwingLow, 90, 2),
		swingAt(structure.SwingHigh, 95, 3), // LowerHigh
		swingAt(structure.SwingLow, 85, 4),  // LowerLow -> LH+LL -> Bearish
	}
	state := structure.BuildLayerState(structure.StructureMicro, swings, 0.05)

	if state.Trend != structure.TrendBearish {
		t.Fatalf("expected TrendBearish, got %v", state.Trend)
	}
	if state.ProtectedHigh == nil || float64(state.ProtectedHigh.Price) != 95 {
		t.Errorf("expected ProtectedHigh at 95, got %+v", state.ProtectedHigh)
	}
}

func TestBuildLayerState_ProtectedLevelPersistsThroughARangeRead(t *testing.T) {
	swings := []structure.Swing{
		swingAt(structure.SwingLow, 100, 1),
		swingAt(structure.SwingHigh, 110, 2),
		swingAt(structure.SwingLow, 105, 3),
		swingAt(structure.SwingHigh, 115, 4), // Bullish established, ProtectedLow=105
		swingAt(structure.SwingHigh, 112, 5), // LowerHigh vs 115 -> mixed signal -> Range
	}
	state := structure.BuildLayerState(structure.StructureMicro, swings, 0.05)

	if state.Trend != structure.TrendRange {
		t.Fatalf("expected the mixed signal to read as Range, got %v", state.Trend)
	}
	if state.ProtectedLow == nil || float64(state.ProtectedLow.Price) != 105 {
		t.Errorf("protected low must persist through a Range read, not clear: got %+v", state.ProtectedLow)
	}
	if state.LastHigh == nil || float64(state.LastHigh.Price) != 112 {
		t.Errorf("LastHigh must still update even while Trend reads Range, got %+v", state.LastHigh)
	}
}

func TestBuildLayerState_IgnoresSwingsFromOtherLayers(t *testing.T) {
	swings := []structure.Swing{
		swingAt(structure.SwingLow, 100, 1),
		{Kind: structure.SwingLow, Layer: structure.StructureMajor, Price: 1, ExcursionPrice: 5, ExcursionATR: 1, ConfirmedAt: 2},
		swingAt(structure.SwingHigh, 110, 3),
	}
	state := structure.BuildLayerState(structure.StructureMicro, swings, 0.05)
	if state.LastLow == nil || float64(state.LastLow.Price) != 100 {
		t.Errorf("the Major-layer swing must not have been folded into the Micro layer's state")
	}
}

func TestBuildLayerState_EmptyInputIsUnknownNotRange(t *testing.T) {
	state := structure.BuildLayerState(structure.StructureMicro, nil, 0.05)
	if state.Trend != structure.TrendUnknown {
		t.Errorf("no swings at all should read Unknown, got %v", state.Trend)
	}
	if state.LastHigh != nil || state.LastLow != nil {
		t.Error("no swings means no LastHigh/LastLow")
	}
}
