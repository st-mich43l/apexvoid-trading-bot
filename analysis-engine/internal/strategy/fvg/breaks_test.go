package fvg

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// swingAt is a fractal swing of two bars on each side (confirmed two bars after it).
func swingAt(index int, kind string, price float64) techniquezone.Swing {
	return techniquezone.Swing{Index: index, Kind: kind, Price: price, ConfirmedIndex: index + 2}
}

// closes are candles that close at the given prices (the body is the whole range: only the close matters to a break).
func closes(prices ...float64) []market.Candle {
	out := make([]market.Candle, len(prices))
	for i, p := range prices {
		out[i] = market.Candle{Time: sweepBase + int64(i)*sweepStep, Open: p, High: p, Low: p, Close: p}
	}
	return out
}

func flatCloses(n int, price float64) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = price
	}
	return out
}

func TestABreakWithTheTrendAsItWasIsABOSEvenIfTheTrendTurnedLater(t *testing.T) {
	// Up to bar 13 the structure is rising (higher highs 20, 22; higher lows 10, 12); afterwards it falls (lower highs 21, 15; lower lows 9, 5). The frame's
	// final trend is "down", so the shared labelling calls the break at bar 13 a change of character; the structure at bar 13 was up and the break is with it.
	swings := []techniquezone.Swing{
		swingAt(2, "low", 10), swingAt(5, "high", 20), swingAt(8, "low", 12), swingAt(11, "high", 22),
		swingAt(20, "high", 21), swingAt(23, "low", 9), swingAt(26, "high", 15), swingAt(29, "low", 5),
	}
	prices := flatCloses(33, 15)
	prices[13] = 23 // closes above the high of 22, confirmed at bar 13
	bars := closes(prices...)

	shared := techniquezone.StructureBreaks(swings, bars, false, 2)
	if len(shared) == 0 || shared[0].Index != 13 || shared[0].Kind != "CHoCH" {
		t.Fatalf("the premise: the shared look-ahead labelling calls it a CHoCH, got %+v", shared)
	}
	got := causalBreaks(swings, bars)
	if len(got) != 1 || got[0].Index != 13 || got[0].Direction != "up" || got[0].Level != 22 || got[0].Kind != "BOS" {
		t.Fatalf("causal breaks = %+v, want one BOS up at bar 13", got)
	}
}

func TestABreakAgainstTheTrendAsItWasIsACHoCH(t *testing.T) {
	swings := []techniquezone.Swing{swingAt(2, "low", 10), swingAt(5, "high", 20), swingAt(8, "low", 12), swingAt(11, "high", 22)}
	prices := flatCloses(20, 15)
	prices[14] = 11 // closes under the low of 12 while the structure is rising
	got := causalBreaks(swings, closes(prices...))
	if len(got) != 1 || got[0].Index != 14 || got[0].Direction != "down" || got[0].Kind != "CHoCH" {
		t.Fatalf("causal breaks = %+v, want one CHoCH down at bar 14", got)
	}
}

func TestABreakIsNeverDatedBeforeItsSwingIsConfirmed(t *testing.T) {
	swings := []techniquezone.Swing{swingAt(5, "high", 20)} // confirmed at bar 7
	prices := flatCloses(12, 15)
	prices[6] = 21 // closes above the high while its right-hand bars are still forming
	bars := closes(prices...)
	if shared := techniquezone.StructureBreaks(swings, bars, false, 2); len(shared) != 1 || shared[0].Index != 6 {
		t.Fatalf("the premise: the shared labelling dates the break at bar 6, got %+v", shared)
	}
	if got := causalBreaks(swings, bars); len(got) != 0 {
		t.Fatalf("bar 6 is before the swing was confirmed (bar 7): %+v", got)
	}
	prices[7] = 21
	got := causalBreaks(swings, closes(prices...))
	if len(got) != 1 || got[0].Index != 7 {
		t.Fatalf("the first close beyond the swing once it is confirmed is the break: %+v", got)
	}
}

func TestASwingIsBrokenOnce(t *testing.T) {
	swings := []techniquezone.Swing{swingAt(2, "low", 10), swingAt(5, "high", 20)}
	prices := flatCloses(16, 15)
	prices[9], prices[10], prices[11] = 21, 22, 23
	if got := causalBreaks(swings, closes(prices...)); len(got) != 1 || got[0].Index != 9 {
		t.Fatalf("breaks = %+v, want the first close only", got)
	}
}

func TestTheImpulseBreakIsTheFirstInTheGapsDirectionWithinItsThreeCandles(t *testing.T) {
	breaks := []techniquezone.Break{
		{Kind: "BOS", Direction: "up", Index: 9},
		{Kind: "CHoCH", Direction: "down", Index: 11},
		{Kind: "CHoCH", Direction: "up", Index: 11},
		{Kind: "BOS", Direction: "up", Index: 13},
	}
	if brk, ok := impulseBreak(breaks, "buy", 10, 12); !ok || brk.Kind != "CHoCH" || brk.Index != 11 || brk.Direction != "up" {
		t.Fatalf("buy: %+v %v", brk, ok)
	}
	if brk, ok := impulseBreak(breaks, "sell", 10, 12); !ok || brk.Direction != "down" {
		t.Fatalf("sell: %+v %v", brk, ok)
	}
	if _, ok := impulseBreak(breaks, "buy", 14, 16); ok {
		t.Fatal("a break outside the impulse is not the impulse's")
	}
}

func TestCausalBreaksDoNotDependOnLaterBars(t *testing.T) {
	swings := []techniquezone.Swing{
		swingAt(2, "low", 10), swingAt(5, "high", 20), swingAt(8, "low", 12), swingAt(11, "high", 22),
		swingAt(20, "high", 21), swingAt(23, "low", 9),
	}
	prices := flatCloses(33, 15)
	prices[13], prices[27] = 23, 8
	bars := closes(prices...)
	whole := causalBreaks(swings, bars)
	for cut := 5; cut < len(bars); cut++ {
		// The frame at `cut` holds only the swings it could have found by then.
		var known []techniquezone.Swing
		for _, s := range swings {
			if s.ConfirmedIndex < cut {
				known = append(known, s)
			}
		}
		for _, b := range causalBreaks(known, bars[:cut]) {
			found := false
			for _, w := range whole {
				found = found || w == b
			}
			if !found {
				t.Fatalf("with %d bars the break %+v is not in the whole frame's %+v", cut, b, whole)
			}
		}
	}
}
