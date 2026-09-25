package structure_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func defaultDisplacement() structure.DisplacementConfig {
	return structure.DisplacementConfig{RangeATR: 1.5, BodyDominance: 0.55}
}

func TestDetectDisplacement_SingleStrongCandleQualifiesImmediately(t *testing.T) {
	// open=100, close=110 (body=10), range=11 (high=110.5, low=99.5):
	// body/range = 10/11 = 0.909 >= 0.55. atr=5 -> netMove=10 -> rangeATR=2.0 >= 1.5.
	bars := []market.Candle{c(1, 100, 110.5, 99.5, 110)}
	disp, ok := structure.DetectDisplacement(bars, 0, 5, 5.0, defaultDisplacement())
	if !ok {
		t.Fatal("expected a qualifying displacement")
	}
	if disp.Direction != market.Buy {
		t.Errorf("expected Buy direction, got %v", disp.Direction)
	}
	if disp.StartTime != 1 || disp.EndTime != 1 {
		t.Errorf("a single-candle displacement should have StartTime==EndTime==1, got %d/%d", disp.StartTime, disp.EndTime)
	}
	if disp.RangeATR < 1.5 {
		t.Errorf("RangeATR should be >= the configured threshold, got %v", disp.RangeATR)
	}
}

func TestDetectDisplacement_ChoppyOverlappingRunNeverQualifies(t *testing.T) {
	// Net-flat over 4 bars (up, down, up, down, ending near the start) —
	// no window size gives a real net move, so no prefix should qualify.
	bars := []market.Candle{
		c(1, 100, 101, 99, 100.5), c(2, 100.5, 101, 99.5, 100),
		c(3, 100, 101, 99, 100.5), c(4, 100.5, 101, 99.5, 100),
	}
	if _, ok := structure.DetectDisplacement(bars, 0, 4, 5.0, defaultDisplacement()); ok {
		t.Error("a net-flat, choppy run must not qualify as displacement")
	}
}

func TestDetectDisplacement_PrefersShortestQualifyingRun(t *testing.T) {
	// Bar 0 alone already qualifies (big directional move); bars 1-2 are
	// weak/choppy filler that would DILUTE a longer-window average. The
	// shortest-prefix scan must return n=1, not keep extending.
	bars := []market.Candle{
		c(1, 100, 110.5, 99.5, 110), // qualifies alone
		c(2, 110, 110.2, 109.8, 109.9),
		c(3, 109.9, 110.1, 109.7, 109.95),
	}
	disp, ok := structure.DetectDisplacement(bars, 0, 3, 5.0, defaultDisplacement())
	if !ok {
		t.Fatal("expected a qualifying displacement")
	}
	if disp.EndTime != bars[0].Time {
		t.Errorf("expected the shortest qualifying run (n=1, EndTime=%d), got EndTime=%d", bars[0].Time, disp.EndTime)
	}
}

func TestDetectDisplacement_OutOfRangeFromIndexOrNonPositiveATR(t *testing.T) {
	bars := []market.Candle{c(1, 100, 110.5, 99.5, 110)}
	if _, ok := structure.DetectDisplacement(bars, 5, 3, 5.0, defaultDisplacement()); ok {
		t.Error("from beyond the candle slice must return false, not panic or fabricate a result")
	}
	if _, ok := structure.DetectDisplacement(bars, 0, 3, 0, defaultDisplacement()); ok {
		t.Error("non-positive ATR must return false (division-by-zero guard)")
	}
}
