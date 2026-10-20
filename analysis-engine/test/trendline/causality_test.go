package trendline_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// zigzagFixture builds a mildly upward-drifting candle series with real
// alternating SwingLow/SwingHigh pivots every 10 bars, and the
// structure.Swing list a real structure.Update pass would have produced
// for them — self-contained (no cross-package structure.Update call, the
// same fixture-construction approach test/zone and test/fib already use)
// so this test proves trendline.Build's own causal contract in
// isolation.
func zigzagFixture(n int) ([]market.Candle, []structure.Swing) {
	candles := make([]market.Candle, n)
	var swings []structure.Swing
	for i := 0; i < n; i++ {
		base := 100 + float64(i)*0.3
		switch i % 20 {
		case 5:
			candles[i] = market.Candle{Time: int64(i), Open: base - 3, High: base - 2, Low: base - 5, Close: base - 3, Volume: 1}
			swings = append(swings, structure.Swing{
				ID: "low", Kind: structure.SwingLow, Price: market.Price(base - 5),
				Time: int64(i), ConfirmedAt: int64(i),
			})
		case 15:
			candles[i] = market.Candle{Time: int64(i), Open: base + 2, High: base + 5, Low: base + 1, Close: base + 2, Volume: 1}
			swings = append(swings, structure.Swing{
				ID: "high", Kind: structure.SwingHigh, Price: market.Price(base + 5),
				Time: int64(i), ConfirmedAt: int64(i),
			})
		default:
			candles[i] = market.Candle{Time: int64(i), Open: base, High: base + 1, Low: base - 1, Close: base, Volume: 1}
		}
	}
	return candles, swings
}

// TestBuild_NeverReferencesDataBeyondGivenCandles is the actual proof
// behind this package's own causality claim (doc.go): every anchor,
// every validation touch, and every break index Build returns for a
// prefix candles[:T] must index strictly within that prefix — never a
// bar Build was not given. Mirrors test/structure/causality_test.go's
// own no-lookahead pattern, applied to this domain's own output shape.
func TestBuild_NeverReferencesDataBeyondGivenCandles(t *testing.T) {
	fullCandles, swings := zigzagFixture(300)
	fullATR := flatATR(300, 5)
	cfg := trendlineTestConfig()

	for _, T := range []int{40, 80, 120, 160, 200, 260, 300} {
		candlesT := fullCandles[:T]
		atrT := fullATR[:T]
		lines := trendline.Build(candlesT, atrT, swings, cfg)
		for _, line := range lines {
			if line.AnchorAIndex >= T || line.AnchorBIndex >= T {
				t.Fatalf("T=%d: line anchors (%d,%d) reference a bar at or beyond T — lookahead violation", T, line.AnchorAIndex, line.AnchorBIndex)
			}
			for _, touch := range line.ValidationTouches {
				if touch.BarIndex >= T {
					t.Fatalf("T=%d: validation touch bar_index=%d references a bar at or beyond T — lookahead violation", T, touch.BarIndex)
				}
				if touch.Time >= int64(T) {
					t.Fatalf("T=%d: validation touch Time=%d references a bar at or beyond T — lookahead violation", T, touch.Time)
				}
			}
			if line.BrokenAt != nil && *line.BrokenAt >= int64(T) {
				t.Fatalf("T=%d: BrokenAt=%d references a bar at or beyond T — lookahead violation", T, *line.BrokenAt)
			}
		}
	}
}

// TestBuild_AnchorPairSurvivesIdenticallyAsMoreDataArrives proves the
// "immutable anchors" half of this domain's own causal contract (doc.go)
// directly: once an anchor pair (A,B) produces a line from a given
// prefix, that SAME pair's Slope/Intercept/AnchorA/AnchorB must be
// byte-identical when recomputed from a LONGER prefix — new data can add
// validations or change State (that's the intended, correct behavior of
// continuous re-evaluation), but must never redraw an already-established
// pair's own geometry.
func TestBuild_AnchorPairSurvivesIdenticallyAsMoreDataArrives(t *testing.T) {
	fullCandles, swings := zigzagFixture(300)
	fullATR := flatATR(300, 5)
	cfg := trendlineTestConfig()

	early := trendline.Build(fullCandles[:120], fullATR[:120], swings, cfg)
	later := trendline.Build(fullCandles[:200], fullATR[:200], swings, cfg)

	for _, e := range early {
		for _, l := range later {
			if e.AnchorAIndex == l.AnchorAIndex && e.AnchorBIndex == l.AnchorBIndex {
				if e.Slope != l.Slope || e.Intercept != l.Intercept {
					t.Fatalf("anchor pair (%d,%d): Slope/Intercept changed as more data arrived: early={%v,%v} later={%v,%v}",
						e.AnchorAIndex, e.AnchorBIndex, e.Slope, e.Intercept, l.Slope, l.Intercept)
				}
				if e.AnchorA != l.AnchorA || e.AnchorB != l.AnchorB {
					t.Fatalf("anchor pair (%d,%d): AnchorA/AnchorB swing IDs changed: early=(%s,%s) later=(%s,%s)",
						e.AnchorAIndex, e.AnchorBIndex, e.AnchorA, e.AnchorB, l.AnchorA, l.AnchorB)
				}
			}
		}
	}
}

// A line is knowable only once its second anchor's swing is confirmed. A swing whose confirmation lies beyond the candles
// given does not exist yet, so it cannot anchor a line, and an anchor that is confirmed reports the candle that confirmed it.
func TestBuild_AnchorNeedsItsConfirmationAndReportsIt(t *testing.T) {
	candles, swings := zigzagFixture(300)
	cfg := trendlineTestConfig()
	for i := range swings {
		swings[i].ConfirmedAt = swings[i].Time + 2 // a right-hand fractal of two candles
	}
	atr := flatATR(300, 5)
	lines := trendline.Build(candles, atr, swings, cfg)
	if len(lines) == 0 {
		t.Fatal("the fixture must yield lines")
	}
	for _, l := range lines {
		if l.AnchorBConfirmedIndex != l.AnchorBIndex+2 {
			t.Errorf("anchor B at %d confirmed at %d, want %d", l.AnchorBIndex, l.AnchorBConfirmedIndex, l.AnchorBIndex+2)
		}
	}
	// Cut the series one candle before the confirmation of the last anchor B: that swing is unconfirmed and no line may use it.
	last := lines[len(lines)-1]
	prefix := candles[:last.AnchorBConfirmedIndex]
	for _, l := range trendline.Build(prefix, atr[:len(prefix)], swings, cfg) {
		if l.AnchorB == last.AnchorB && l.AnchorBIndex == last.AnchorBIndex && l.AnchorAIndex == last.AnchorAIndex {
			t.Fatalf("a line was drawn through a swing whose confirmation had not closed: %+v", l)
		}
	}
}
