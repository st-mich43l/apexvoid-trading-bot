package demand

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The confirmation episode of this strategy: a touch of the entry band followed, on or after the touch bar, by one of the recognised rejection patterns.
// The pattern priority, windows and thresholds are this package's own.

const (
	patternWickRejection  = "wick_rejection"
	patternSweepReclaim   = "sweep_reclaim"
	patternRejectionCHoCH = "rejection_choch"
	patternStrongReclaim  = "strong_reclaim"
	patternEngulfing      = "engulfing"

	epsilonPrice = 1e-12
)

// grabRef is a liquidity grab (sweep then reclaim) the frame found on one bar; only grades A and B confirm.
type grabRef struct {
	Index int
	Grade string
}

type confirmParams struct {
	Direction                string // "BUY" | "SELL"
	Low, High                float64
	TouchLookback            int
	ConfirmLookback          int
	Grabs                    []grabRef
	HasCHoCH                 bool
	ATR                      float64
	EngulfingMinimumRangeATR float64
	MinIndex                 int
}

type confirmation struct {
	Type              string
	TouchIndex        int
	ConfirmationIndex int
	TouchTime         int64
	ConfirmationTime  int64
}

// evaluateConfirmation finds the latest valid confirmation within the lookback windows, or nil. Touch and confirmation may be on different bars; the
// confirmation is on or after its touch.
func evaluateConfirmation(candles []market.Candle, p confirmParams) *confirmation {
	if len(candles) == 0 {
		return nil
	}
	touchLB := p.TouchLookback
	if touchLB < 1 {
		touchLB = 1
	}
	confirmLB := touchLB
	if p.ConfirmLookback > 0 {
		confirmLB = p.ConfirmLookback
	}
	last := len(candles) - 1
	touchEarliest := maxInt(maxInt(0, p.MinIndex), last-touchLB+1)
	confirmEarliest := maxInt(maxInt(0, p.MinIndex), last-confirmLB+1)

	var touchIndexes []int
	for i := touchEarliest; i <= last; i++ {
		if candles[i].Low <= p.High+epsilonPrice && candles[i].High >= p.Low-epsilonPrice {
			touchIndexes = append(touchIndexes, i)
		}
	}
	if len(touchIndexes) == 0 {
		return nil
	}
	grabGrade := make(map[int]string, len(p.Grabs))
	for _, g := range p.Grabs {
		if confirmEarliest <= g.Index && g.Index <= last {
			grabGrade[g.Index] = g.Grade
		}
	}
	// The latest valid confirmation, so a stale touch without a fresh confirmation never executes.
	for ci := last; ci >= confirmEarliest; ci-- {
		touchIndex := -1
		for _, ti := range touchIndexes {
			if ti <= ci {
				touchIndex = ti
			}
		}
		if touchIndex < 0 {
			continue
		}
		bar := candles[ci]
		var kind string
		switch {
		case grabGrade[ci] == "A" || grabGrade[ci] == "B":
			kind = patternSweepReclaim
		case wickRejection(bar, p.Direction) && p.HasCHoCH:
			kind = patternRejectionCHoCH
		case strongReclaim(bar, p.Direction, p.Low, p.High):
			kind = patternStrongReclaim
		case wickRejection(bar, p.Direction):
			kind = patternWickRejection
		case ci > 0 && engulfing(bar, candles[ci-1], p.Direction, p.ATR, p.EngulfingMinimumRangeATR):
			kind = patternEngulfing
		}
		if kind == "" {
			continue
		}
		return &confirmation{Type: kind, TouchIndex: touchIndex, ConfirmationIndex: ci, TouchTime: candles[touchIndex].Time, ConfirmationTime: candles[ci].Time}
	}
	return nil
}

// wickRejection: the rejecting wick is at least the body and the bar closes in its reaction-side third, in the reaction direction.
func wickRejection(c market.Candle, direction string) bool {
	rng := c.High - c.Low
	if rng <= 0 {
		return false
	}
	body := abs(c.Close - c.Open)
	upper := c.High - maxFloat(c.Open, c.Close)
	lower := minFloat(c.Open, c.Close) - c.Low
	lowerThird := c.Low + rng/3
	upperThird := c.High - rng/3
	if direction == "SELL" {
		return upper >= body && c.Close < c.Open && c.Close <= lowerThird
	}
	return lower >= body && c.Close > c.Open && c.Close >= upperThird
}

// strongReclaim: the wick pierces the band's far edge, then the bar closes back through the near edge in the reaction direction.
func strongReclaim(c market.Candle, direction string, low, high float64) bool {
	if direction == "BUY" {
		swept := c.Low < low-epsilonPrice
		reclaimed := c.Close >= low-epsilonPrice && c.Close > c.Open
		return swept && reclaimed
	}
	swept := c.High > high+epsilonPrice
	reclaimed := c.Close <= high+epsilonPrice && c.Close < c.Open
	return swept && reclaimed
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
