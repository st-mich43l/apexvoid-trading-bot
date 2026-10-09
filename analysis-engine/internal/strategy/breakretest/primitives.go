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

// atrWindow is the Wilder ATR(n) as of any index, computed over a FIXED
// trailing window of candles ending at that index (see the identical, tested
// reasoning in the CRT package): the value is a pure function of those candles,
// independent of how much older history is loaded.
type atrWindow struct {
	bars    []market.Candle
	length  int
	window  int
	values  []float64
	checked []bool
}

func newATRWindow(bars []market.Candle, length, window int) *atrWindow {
	return &atrWindow{bars: bars, length: length, window: window, values: make([]float64, len(bars)), checked: make([]bool, len(bars))}
}

func (a *atrWindow) at(i int) (float64, bool) {
	if a == nil || i < 0 || i >= len(a.bars) {
		return 0, false
	}
	if !a.checked[i] {
		a.checked[i] = true
		start := i - a.window + 1
		if start < 0 {
			start = 0
		}
		if series, ok := indicator.WilderATR(a.bars[start:i+1], a.length); ok {
			a.values[i] = series[len(series)-1]
		}
	}
	v := a.values[i]
	if math.IsNaN(v) || math.IsInf(v, 0) || v <= 0 {
		return 0, false
	}
	return v, true
}

// validSeries is true when the candles are finite, internally consistent and
// strictly increasing in time. A malformed series never produces a setup.
func validSeries(bars []market.Candle) bool {
	for i, c := range bars {
		for _, v := range []float64{c.Open, c.High, c.Low, c.Close} {
			if math.IsNaN(v) || math.IsInf(v, 0) {
				return false
			}
		}
		if c.High < c.Low || c.High < math.Max(c.Open, c.Close) || c.Low > math.Min(c.Open, c.Close) {
			return false
		}
		if i > 0 && c.Time <= bars[i-1].Time {
			return false
		}
	}
	return true
}

// mirror reflects prices about zero so one (bullish) orientation of the
// detector serves both directions: a broken support retested from below on the
// original series is a broken resistance retested from above on the mirror.
func mirror(bars []market.Candle) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, c := range bars {
		out[i] = market.Candle{Time: c.Time, Open: -c.Open, High: -c.Low, Low: -c.High, Close: -c.Close, Volume: c.Volume}
	}
	return out
}

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
