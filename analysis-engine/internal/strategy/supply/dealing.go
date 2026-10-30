package supply

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// swingZigzagATR is the zigzag filter of the structure swings: a swing counts once price moved this many ATR away from the previous one (the value the
// frozen analysis used for every frame).
const swingZigzagATR = 1.0

// dealingRange is the range a supply zone's premium or discount is read in: on the zone's own structure frame, as of the decision bar, never on a range price
// has already left.
//
//	swingHigh / swingLow  the most recent confirmed swing high and low of the frame (fractals of structure_pivot_bars bars on each side)
//	rangeHigh             the highest of swingHigh and every high after it: the frame's bars up to its last closed bar, then the M5 bars since that close
//	rangeLow              the lowest of swingLow and every low after it, the same way
//
// In a rally the range is [last swing low, running high]; in a sell-off [running low, last swing high]; in a range it is the last swing pair. It always
// contains the latest price, so a breakout extends the range instead of falling back to one price has broken out of. ok is false without both swings.
type dealingBand struct {
	High, Low float64
	OK        bool
}

func (d dealingBand) equilibrium() float64 { return (d.High + d.Low) / 2 }

// position is where p sits in the range: 0 at the low, 1 at the high (NaN without a range).
func (d dealingBand) position(p float64) float64 {
	if !d.OK || d.High <= d.Low {
		return math.NaN()
	}
	return (p - d.Low) / (d.High - d.Low)
}

func dealingRange(bars []market.Candle, atr []float64, tf market.Timeframe, execBars []market.Candle, pivotBars int) dealingBand {
	if len(bars) == 0 {
		return dealingBand{}
	}
	var high, low *techniquezone.Swing
	swings := techniquezone.FindSwings(bars, pivotBars, 0, swingZigzagATR, atr, -1)
	for i := range swings {
		s := &swings[i]
		if s.ConfirmedIndex >= len(bars) {
			continue // not confirmed by a closed bar of this frame
		}
		switch s.Kind {
		case "high":
			if high == nil || s.Index > high.Index {
				high = s
			}
		case "low":
			if low == nil || s.Index > low.Index {
				low = s
			}
		}
	}
	if high == nil || low == nil {
		return dealingBand{}
	}
	minutes, ok := tf.Minutes()
	if !ok {
		return dealingBand{}
	}
	closed := bars[len(bars)-1].Time + int64(minutes)*60 // the first instant the frame's last bar is closed
	rangeHigh, rangeLow := high.Price, low.Price
	for _, b := range bars[high.Index+1:] {
		rangeHigh = math.Max(rangeHigh, b.High)
	}
	for _, b := range bars[low.Index+1:] {
		rangeLow = math.Min(rangeLow, b.Low)
	}
	for _, b := range execBars {
		if b.Time >= closed {
			rangeHigh, rangeLow = math.Max(rangeHigh, b.High), math.Min(rangeLow, b.Low)
		}
	}
	return dealingBand{High: rangeHigh, Low: rangeLow, OK: rangeHigh > rangeLow}
}
