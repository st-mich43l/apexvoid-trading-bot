package brreplay_test

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// position is one published opportunity as the simulator sees it.
type position struct {
	Version      string
	ID           string
	StructuralID string
	Direction    market.Direction
	EntryLow     float64
	EntryHigh    float64
	Stop         float64
	Target       float64
	// ObservedAt is the OPEN time of the M5 candle whose close first showed it.
	ObservedAt int64
	ExpiresAt  int64
	ATR        float64
}

func (p position) proximal() float64 {
	if p.Direction == market.Sell {
		return p.EntryLow
	}
	return p.EntryHigh
}

// outcome is a HYPOTHETICAL result under causal fill rules; it is never
// presented as realised performance.
type outcome struct {
	Result           string // unfilled | target | stop | timeout | invalid
	FilledAt         int64
	BarsToFill       int
	R                float64
	MFE, MAE         float64 // in units of the filled risk
	SameBarAmbiguous bool
}

// holdBars bounds how long a filled position is followed (8 hours of M5).
const holdBars = 96

// simulate places the order when the observing candle closes and fills it only
// on a LATER candle:
//
//   - BUY fills when a candle's low reaches the proximal (upper) edge, at the
//     proximal price, or at the open when the candle opens below it; SELL mirrors.
//   - the fill candle itself can only stop the position out (the intra-candle
//     order of fill and target is unknowable, so a target on the fill candle is
//     never credited);
//   - later candles check the stop first; a candle touching both is flagged
//     ambiguous and resolved as a stop;
//   - an order unfilled at expiry is cancelled; a position still open after
//     holdBars is marked to the close.
func simulate(bars []market.Candle, p position) outcome {
	obs := -1
	for i, b := range bars {
		if b.Time == p.ObservedAt {
			obs = i
			break
		}
	}
	if obs < 0 {
		return outcome{Result: "invalid"}
	}
	sell := p.Direction == market.Sell
	fillIdx, fill := -1, 0.0
	for j := obs + 1; j < len(bars) && bars[j].Time < p.ExpiresAt; j++ {
		b := bars[j]
		if !sell && b.Low <= p.EntryHigh {
			fillIdx, fill = j, math.Min(b.Open, p.EntryHigh)
			break
		}
		if sell && b.High >= p.EntryLow {
			fillIdx, fill = j, math.Max(b.Open, p.EntryLow)
			break
		}
	}
	if fillIdx < 0 {
		return outcome{Result: "unfilled"}
	}
	risk := fill - p.Stop
	if sell {
		risk = p.Stop - fill
	}
	if !(risk > 0) {
		return outcome{Result: "invalid", FilledAt: bars[fillIdx].Time, BarsToFill: fillIdx - obs - 1}
	}
	o := outcome{FilledAt: bars[fillIdx].Time, BarsToFill: fillIdx - obs - 1}
	excursion := func(b market.Candle) {
		var favourable, adverse float64
		if sell {
			favourable, adverse = (fill-b.Low)/risk, (b.High-fill)/risk
		} else {
			favourable, adverse = (b.High-fill)/risk, (fill-b.Low)/risk
		}
		o.MFE, o.MAE = math.Max(o.MFE, favourable), math.Max(o.MAE, adverse)
	}
	stopHit := func(b market.Candle) bool {
		if sell {
			return b.High >= p.Stop
		}
		return b.Low <= p.Stop
	}
	targetHit := func(b market.Candle) bool {
		if sell {
			return b.Low <= p.Target
		}
		return b.High >= p.Target
	}
	// The fill candle: stop only.
	excursion(bars[fillIdx])
	if stopHit(bars[fillIdx]) {
		o.Result, o.R = "stop", -1
		return o
	}
	last := fillIdx
	for k := fillIdx + 1; k < len(bars) && k <= fillIdx+holdBars; k++ {
		b := bars[k]
		last = k
		excursion(b)
		s, tp := stopHit(b), targetHit(b)
		if s && tp {
			o.SameBarAmbiguous = true
		}
		if s {
			o.Result, o.R = "stop", -1
			return o
		}
		if tp {
			reward := p.Target - fill
			if sell {
				reward = fill - p.Target
			}
			o.Result, o.R = "target", reward/risk
			return o
		}
	}
	o.Result = "timeout"
	if sell {
		o.R = (fill - bars[last].Close) / risk
	} else {
		o.R = (bars[last].Close - fill) / risk
	}
	return o
}
