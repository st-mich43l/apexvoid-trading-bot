package regime_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
)

func candle(t int64, open, high, low, close float64) market.Candle {
	return market.Candle{Time: t, Open: open, High: high, Low: low, Close: close}
}

func TestDisplacementGradeMatchesPythonBodyAndRangeRules(t *testing.T) {
	if !regime.DisplacementGrade(candle(1, 90, 101, 90, 100), 5, "up") {
		t.Fatal("expected a bullish candle with a dominant body and one-ATR range to grade")
	}
	if regime.DisplacementGrade(candle(1, 100, 101, 90, 99), 5, "up") {
		t.Fatal("bearish candle must not grade as upward displacement")
	}
	if regime.DisplacementGrade(candle(1, 100, 100.5, 100, 100.4), 5, "up") {
		t.Fatal("sub-ATR candle must not grade as displacement")
	}
}

func TestAcceptedBoxBreakUsesDisplacementOrConsecutiveCloses(t *testing.T) {
	atr := []float64{2, 2, 2}
	strong := regime.AcceptedBoxBreak([]market.Candle{
		candle(1, 95, 99, 94, 98),
		candle(2, 99, 104, 98, 103),
	}, atr, 100, 90, true, 0.1, 2)
	if strong == nil || strong.Direction != "up" || strong.Acceptance != "displacement" || !strong.Coiling {
		t.Fatalf("unexpected displacement acceptance: %+v", strong)
	}

	closeHold := regime.AcceptedBoxBreak([]market.Candle{
		candle(1, 95, 101, 94, 101),
		candle(2, 101, 103, 100, 101),
	}, []float64{10, 10}, 100, 90, false, 0, 2)
	if closeHold == nil || closeHold.Acceptance != "2 closes" {
		t.Fatalf("expected consecutive-close acceptance, got %+v", closeHold)
	}
}

func TestClassifyPreservesPythonChopReasonsAndRangeGeometry(t *testing.T) {
	candles := []market.Candle{
		candle(1, 94, 98, 92, 95), candle(2, 95, 99, 93, 96),
		candle(3, 96, 98, 94, 95), candle(4, 95, 97, 93, 94),
	}
	state := regime.Classify(candles, []float64{5, 5, 5, 5}, nil, "range", 100, 90, true, regime.Config{ChopFilterEnabled: true, ChopLookback: 4, ChopRangeATR: 4, CoilContract: 0.8, EqualToleranceATR: 0.05, BreakoutAcceptBars: 2})
	if state.Kind != "chop" || state.LegacyKind != "chop" || state.NewKind != "chop" {
		t.Fatalf("expected chop state, got %+v", state)
	}
	if state.RangeHigh != 100 || state.RangeLow != 90 || state.HeightATR != 2 {
		t.Fatalf("unexpected range geometry: %+v", state)
	}
	if len(state.Reasons) != 2 {
		t.Fatalf("expected both range-height and held-range reasons, got %v", state.Reasons)
	}
}
