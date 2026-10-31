package fvgv8

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The confirmation episode of this strategy (docs/strategies/fvg.md). On M5, after the gap exists, a TOUCH trades into the entry band without closing
// beyond the gap's far edge; within ConfirmationWindowBars of it (the touch bar included) one bar must reject:
//
//   - wick_rejection: it closes beyond the band's proximal edge in the trade direction (a BUY closes above the band high) with a rejecting wick of at
//     least MinimumWickRatio of its range, in the trade direction;
//   - engulfing: it is a bullish (bearish) engulfing close of the prior bar, with a range of at least EngulfingMinimumRangeATR of the M5 ATR.
//
// The rejection must not be older than ConfirmationMaxAgeBars at the decision bar. Sweep-reclaim, strong-reclaim and rejection-CHoCH are not
// confirmations of a gap.

const (
	patternWickRejection = "wick_rejection"
	patternEngulfing     = "engulfing"

	epsilonPrice = 1e-12
)

type confirmation struct {
	Type              string
	TouchIndex        int
	ConfirmationIndex int
	TouchTime         int64
	ConfirmationTime  int64
}

// touches reports whether bar c is a touch of the band that does not close beyond the gap's far edge.
func touches(c market.Candle, direction market.Direction, bandLow, bandHigh, structLow, structHigh float64) bool {
	if c.Low > bandHigh+epsilonPrice || c.High < bandLow-epsilonPrice {
		return false
	}
	if direction == market.Buy {
		return c.Close >= structLow-epsilonPrice
	}
	return c.Close <= structHigh+epsilonPrice
}

// rejects names the pattern bar c shows as a rejection of the band, or "".
func (v *view) rejects(bars []market.Candle, c int, bandLow, bandHigh float64) string {
	bar := bars[c]
	rng := bar.High - bar.Low
	if rng > 0 {
		if v.dir == market.Buy {
			lower := minFloat(bar.Open, bar.Close) - bar.Low
			if bar.Close > bandHigh+epsilonPrice && bar.Close > bar.Open && lower/rng >= v.cfg.MinimumWickRatio {
				return patternWickRejection
			}
		} else {
			upper := bar.High - maxFloat(bar.Open, bar.Close)
			if bar.Close < bandLow-epsilonPrice && bar.Close < bar.Open && upper/rng >= v.cfg.MinimumWickRatio {
				return patternWickRejection
			}
		}
	}
	if c > 0 && engulfing(bar, bars[c-1], string(v.dir), v.atr, v.cfg.EngulfingMinimumRangeATR) {
		return patternEngulfing
	}
	return ""
}

// confirm looks for the latest rejection of the band on the execution bars. When there is a touch to confirm but no rejection it returns
// refusedNoRejection; when the latest rejection is older than the allowed age, refusedStaleConfirmation; when there has been no touch in reach, nothing
// (the gap is simply not in play).
func (v *view) confirm(in instance, bandLow, bandHigh, structLow, structHigh float64) (*confirmation, string) {
	bars := v.frame.Bars
	last := len(bars) - 1
	window := v.cfg.ConfirmationWindowBars
	earliest := maxInt(in.TouchableFrom, 0)
	reach := maxInt(earliest, last-(window-1)-v.cfg.ConfirmationMaxAgeBars)
	touched := false
	for i := reach; i <= last; i++ {
		if touches(bars[i], v.dir, bandLow, bandHigh, structLow, structHigh) {
			touched = true
			break
		}
	}
	if !touched {
		return nil, ""
	}
	for c := last; c >= earliest; c-- {
		touchIndex := -1
		for t := maxInt(earliest, c-window+1); t <= c; t++ {
			if touches(bars[t], v.dir, bandLow, bandHigh, structLow, structHigh) {
				touchIndex = t // the latest touch at or before the rejection, inside its window
			}
		}
		if touchIndex < 0 {
			continue
		}
		kind := v.rejects(bars, c, bandLow, bandHigh)
		if kind == "" {
			continue
		}
		if last-c > v.cfg.ConfirmationMaxAgeBars {
			return nil, refusedStaleConfirmation
		}
		return &confirmation{Type: kind, TouchIndex: touchIndex, ConfirmationIndex: c, TouchTime: bars[touchIndex].Time, ConfirmationTime: bars[c].Time}, ""
	}
	return nil, refusedNoRejection
}

// engulfing: this bar's body fully engulfs the prior bar's body, the prior bar was a doji or opposed the reaction, and (with an ATR) the bar has real
// range.
func engulfing(c, prior market.Candle, direction string, atr, minimumRangeATR float64) bool {
	bodyLow, bodyHigh := minFloat(c.Open, c.Close), maxFloat(c.Open, c.Close)
	priorBodyLow, priorBodyHigh := minFloat(prior.Open, prior.Close), maxFloat(prior.Open, prior.Close)
	if bodyLow > priorBodyLow || bodyHigh < priorBodyHigh {
		return false
	}
	priorRange := maxFloat(prior.High-prior.Low, epsilonPrice)
	priorBodyFraction := abs(prior.Close-prior.Open) / priorRange
	priorBullish := prior.Close > prior.Open+epsilonPrice
	priorBearish := prior.Close < prior.Open-epsilonPrice
	priorOpposite := priorBodyFraction < 0.1 || (direction == "BUY" && priorBearish) || (direction == "SELL" && priorBullish)
	if !priorOpposite {
		return false
	}
	if atr > 0 && (c.High-c.Low) < maxFloat(0, minimumRangeATR)*atr {
		return false
	}
	if direction == "BUY" {
		return c.Close > c.Open
	}
	return c.Close < c.Open
}

func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
