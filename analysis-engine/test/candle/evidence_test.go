package candle_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/candle"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func TestGeometryZeroRangeIsFiniteAndNeutral(t *testing.T) {
	g := candle.GeometryOf(market.Candle{Open: 100, High: 100, Low: 100, Close: 100}, 2)
	if g.CloseLocation != .5 || g.BodyFraction != 0 || g.RangeATR != 0 {
		t.Fatalf("unexpected zero-range geometry: %+v", g)
	}
}

func TestEvaluateSweepReclaimAndDisplacement(t *testing.T) {
	bars := []market.Candle{
		{Open: 100, High: 101, Low: 99.5, Close: 100.1},
		{Open: 100.1, High: 100.4, Low: 99.9, Close: 100.2},
		{Open: 100.0, High: 102.0, Low: 100.0, Close: 101.8},
	}
	low, high := 100.0, 101.0
	got := candle.Evaluate(bars, "BUY", .5, low, &low, &high)
	if got == nil || got.FinalScore <= 0 || got.PrimaryPattern == "" {
		t.Fatalf("expected candle evidence, got %+v", got)
	}
	if got.Version != candle.Version || got.Direction != "BUY" {
		t.Fatalf("unexpected evidence identity: %+v", got)
	}
}

func TestEvaluateDoesNotCreateEvidenceForNeutralBars(t *testing.T) {
	bars := []market.Candle{
		{Open: 100.09, High: 100.1, Low: 99.99, Close: 100},
		{Open: 100.09, High: 100.1, Low: 99.99, Close: 100},
	}
	if got := candle.Evaluate(bars, "BUY", .5, 99.5, nil, nil); got != nil {
		t.Fatalf("neutral bars must not create directional evidence: %+v", got)
	}
}
