package fvg

import (
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Structure breaks, computed causally by this strategy (docs/strategies/liquidity-sweep-and-structure.md). The frame's shared breaks
// (LegacyFrame.Breaks) date a break up to fractal_n bars early and label BOS against CHoCH with the trend as of the LAST bar; this is the algorithm of
// techniquezone.breaksCausal, copied here because a strategy owns what it reads (the duplication is intended):
//
//   - a swing joins the reference set only at the bar that confirms it (its right-hand bars have closed);
//   - a close beyond the most recent confirmed swing of the other side is a break at that bar, once per swing price;
//   - BOS or CHoCH is decided by the market structure of the swings confirmed BEFORE that bar, never by the trend as of the frame's last bar.

const (
	breakBOS   = "BOS"
	breakCHoCH = "CHoCH"
)

type brokenKey struct {
	direction string
	level     float64
}

// confirmedAt is the index of the bar that closes a swing's right-hand bars.
func confirmedAt(swing techniquezone.Swing) int {
	if swing.ConfirmedIndex > swing.Index {
		return swing.ConfirmedIndex
	}
	return swing.Index + 1
}

// causalBreaks are the structure breaks of a frame as they were knowable at each of its closed bars.
func causalBreaks(swings []techniquezone.Swing, bars []market.Candle) []techniquezone.Break {
	var out []techniquezone.Break
	broken := map[brokenKey]bool{}
	ordered := append([]techniquezone.Swing(nil), swings...)
	sort.SliceStable(ordered, func(a, b int) bool { return confirmedAt(ordered[a]) < confirmedAt(ordered[b]) })
	next, trend := 0, "range"
	var confirmed []techniquezone.Swing
	for i := range bars {
		advanced := false
		for next < len(ordered) && confirmedAt(ordered[next]) <= i {
			confirmed = append(confirmed, ordered[next])
			next++
			advanced = true
		}
		if advanced {
			trend = techniquezone.MarketStructure(confirmed)
		}
		emitBreaks(confirmed, trend, bars[i].Close, i, broken, &out)
	}
	return out
}

func lastOfKind(swings []techniquezone.Swing, kind string) (techniquezone.Swing, bool) {
	for i := len(swings) - 1; i >= 0; i-- {
		if swings[i].Kind == kind {
			return swings[i], true
		}
	}
	return techniquezone.Swing{}, false
}

func emitBreaks(prior []techniquezone.Swing, trend string, close float64, i int, broken map[brokenKey]bool, out *[]techniquezone.Break) {
	if high, ok := lastOfKind(prior, "high"); ok {
		key := brokenKey{"up", high.Price}
		if close > high.Price && !broken[key] {
			broken[key] = true
			*out = append(*out, techniquezone.Break{Kind: breakKind(trend, "up"), Direction: "up", Level: high.Price, Index: i})
		}
	}
	if low, ok := lastOfKind(prior, "low"); ok {
		key := brokenKey{"down", low.Price}
		if close < low.Price && !broken[key] {
			broken[key] = true
			*out = append(*out, techniquezone.Break{Kind: breakKind(trend, "down"), Direction: "down", Level: low.Price, Index: i})
		}
	}
}

// breakKind: a break with the trend is a BOS, against it a CHoCH; with no trend yet it is a BOS.
func breakKind(trend, direction string) string {
	if trend == "up" || trend == "down" {
		if trend == direction {
			return breakBOS
		}
		return breakCHoCH
	}
	return breakBOS
}

// impulseBreak is the first causal break in the direction of the gap by one of the candles of its impulse (the gap's first to third candle), and whether
// there is one. A measured fact: nothing reads it to decide.
func impulseBreak(breaks []techniquezone.Break, side string, first, third int) (techniquezone.Break, bool) {
	wanted := "down"
	if side == "buy" {
		wanted = "up"
	}
	for _, brk := range breaks {
		if brk.Direction == wanted && brk.Index >= first && brk.Index <= third {
			return brk, true
		}
	}
	return techniquezone.Break{}, false
}
