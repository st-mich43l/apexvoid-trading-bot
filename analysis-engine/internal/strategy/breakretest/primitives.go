package breakretest

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Neutral candle and series primitives. They carry no Break & Retest decision.

const (
	m5Seconds = int64(300)
	epsilon   = 1e-12
)

func newATRWindow(bars []market.Candle, length, window int) *indicator.WilderWindow {
	return indicator.NewWilderWindow(bars, length, window)
}

func validSeries(bars []market.Candle) bool { return market.ValidSeries(bars) }

// mirror reflects prices about zero (see market.Mirror).
func mirror(bars []market.Candle) []market.Candle { return market.Mirror(bars) }

// pivot is a fractal extreme. A pivot at index p is only knowable once candle
// p+n has closed (conf = p+n).
type pivot struct {
	index int
	conf  int
	price float64
}

// pivotHighs: the high is strictly above the n candles before it and at least
// the n after it. The asymmetry keeps one touch of an equal-high plateau as one
// pivot (the first) while two equal highs separated by more than n candles are
// two touches, which is what makes a double top a level.
func pivotHighs(bars []market.Candle, n int) []pivot {
	var out []pivot
	for p := n; p+n < len(bars); p++ {
		ok := true
		for k := 1; k <= n && ok; k++ {
			if bars[p-k].High >= bars[p].High || bars[p+k].High > bars[p].High {
				ok = false
			}
		}
		if ok {
			out = append(out, pivot{index: p, conf: p + n, price: bars[p].High})
		}
	}
	return out
}

// pivotLows is the mirror of pivotHighs.
func pivotLows(bars []market.Candle, n int) []pivot {
	var out []pivot
	for p := n; p+n < len(bars); p++ {
		ok := true
		for k := 1; k <= n && ok; k++ {
			if bars[p-k].Low <= bars[p].Low || bars[p+k].Low < bars[p].Low {
				ok = false
			}
		}
		if ok {
			out = append(out, pivot{index: p, conf: p + n, price: bars[p].Low})
		}
	}
	return out
}

func rangeOf(c market.Candle) float64 { return c.High - c.Low }

// bodyRatio is |close-open| / range.
func bodyRatio(c market.Candle) float64 {
	return math.Abs(c.Close-c.Open) / math.Max(rangeOf(c), epsilon)
}

// closeStrength is where the close sits in the candle: 1 at the high, 0 at the
// low (bullish orientation).
func closeStrength(c market.Candle) float64 {
	return (c.Close - c.Low) / math.Max(rangeOf(c), epsilon)
}

// lowerWickRatio is the lower wick as a fraction of the candle range.
func lowerWickRatio(c market.Candle) float64 {
	return (math.Min(c.Open, c.Close) - c.Low) / math.Max(rangeOf(c), epsilon)
}
