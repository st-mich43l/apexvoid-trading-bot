package structure_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// ohlc is a compact per-bar fixture; buildCandles turns a slice of these
// into market.Candle values with Time = 1, 2, 3, ... (index+1), so every
// test can reference bars[i].Time directly instead of hardcoding a
// timestamp.
type ohlc struct{ o, h, l, cl float64 }

func buildCandles(series []ohlc) []market.Candle {
	out := make([]market.Candle, len(series))
	for i, s := range series {
		out[i] = c(int64(i+1), s.o, s.h, s.l, s.cl)
	}
	return out
}

func TestDetectPivots_FindsThePeakNotItsNeighbors(t *testing.T) {
	bars := buildCandles([]ohlc{
		{10, 10, 9, 10}, {10, 11, 10, 11}, {11, 12, 11, 12},
		{12, 15, 14, 14}, {14, 12, 11, 12}, {12, 11, 10, 11}, {11, 10, 9, 10},
	})
	atr := flatATR(len(bars), 1.0)

	pivots := structure.DetectPivots(bars, 2, 2, atr)

	if len(pivots) != 1 {
		t.Fatalf("expected exactly 1 pivot, got %d: %+v", len(pivots), pivots)
	}
	p := pivots[0]
	if p.Kind != structure.PivotHigh {
		t.Errorf("expected PivotHigh, got %v", p.Kind)
	}
	if p.BarIndex != 3 {
		t.Errorf("expected BarIndex 3 (the actual peak), got %d", p.BarIndex)
	}
	if float64(p.Price) != 15 {
		t.Errorf("expected Price 15, got %v", p.Price)
	}
	if p.Time != bars[3].Time {
		t.Errorf("Time should be the pivot bar's own time")
	}
	if p.ConfirmedAt != bars[5].Time {
		t.Errorf("ConfirmedAt should be candles[BarIndex+rightBars].Time (index 5), got %d want %d", p.ConfirmedAt, bars[5].Time)
	}
	if p.ExcursionPrice != 5 {
		t.Errorf("expected weaker-side excursion 5 (min(15-10, 15-10)), got %v", p.ExcursionPrice)
	}
	if p.Strength != 5 { // atr=1.0, so Strength == ExcursionPrice
		t.Errorf("expected Strength 5 at atr=1.0, got %v", p.Strength)
	}
}

func TestDetectPivots_FindsTheTrough(t *testing.T) {
	bars := buildCandles([]ohlc{
		{20, 20, 19, 19}, {19, 19, 18, 18}, {18, 18, 17, 17},
		{17, 15, 14, 14}, {14, 18, 17, 17}, {17, 19, 18, 18}, {18, 20, 19, 19},
	})
	atr := flatATR(len(bars), 1.0)

	pivots := structure.DetectPivots(bars, 2, 2, atr)

	if len(pivots) != 1 {
		t.Fatalf("expected exactly 1 pivot, got %d: %+v", len(pivots), pivots)
	}
	p := pivots[0]
	if p.Kind != structure.PivotLow {
		t.Errorf("expected PivotLow, got %v", p.Kind)
	}
	if p.BarIndex != 3 || float64(p.Price) != 14 {
		t.Errorf("expected trough at index 3, price 14; got index %d price %v", p.BarIndex, p.Price)
	}
}

func TestDetectPivots_InsufficientHistoryFindsNothing(t *testing.T) {
	bars := buildCandles([]ohlc{
		{10, 10, 9, 10}, {10, 11, 10, 11}, {11, 12, 11, 12},
	})
	atr := flatATR(len(bars), 1.0)

	pivots := structure.DetectPivots(bars, 2, 2, atr)
	if len(pivots) != 0 {
		t.Errorf("expected no pivots (window too short for left=2,right=2), got %d", len(pivots))
	}
}

func TestDetectPivots_FlatMarketFindsNothing(t *testing.T) {
	series := make([]ohlc, 10)
	for i := range series {
		series[i] = ohlc{100, 101, 99, 100}
	}
	bars := buildCandles(series)
	atr := flatATR(len(bars), 1.0)

	pivots := structure.DetectPivots(bars, 2, 2, atr)
	if len(pivots) != 0 {
		t.Errorf("a flat market has no strict local extremum; expected 0 pivots, got %d", len(pivots))
	}
}
