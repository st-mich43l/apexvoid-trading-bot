package crt

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Neutral candle and series primitives. They carry no CRT decision.

const (
	m5Seconds = int64(300)
	h1Seconds = int64(3600)
	epsilon   = 1e-12
)

// atrWindow is the Wilder ATR(n) of a candle series as of any index, computed
// over a FIXED trailing window of window candles ending at that index, never
// over the whole loaded history. Wilder smoothing is recursive, so its value
// carries a decaying residual of the seed; computing it over the whole history
// would make a CRT's geometry depend (slightly) on how many candles happened to
// be loaded. A fixed trailing window removes that dependence exactly: for any
// index with at least window candles behind it the value is a pure function of
// those candles. Each window is the repository's indicator.WilderATR (TR is
// max(H-L, |H-prevC|, |L-prevC|); ATR[t] = (ATR[t-1]*(n-1) + TR[t]) / n, seeded
// with the mean of the first n true ranges).
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

// at is the ATR as of index i when it exists, is finite and is positive. It
// needs at least length+1 candles ending at i (the indicator's own minimum).
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

// enough reports whether the series is long enough to carry any ATR.
func (a *atrWindow) enough() bool { return a != nil && len(a.bars) >= a.length+1 }

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

// firstAtOrAfter is the index of the first candle whose open time is >= t, or
// len(bars) when none is.
func firstAtOrAfter(bars []market.Candle, t int64) int {
	return sort.Search(len(bars), func(i int) bool { return bars[i].Time >= t })
}

// mirror reflects prices about zero (open/close negate, high and low swap and
// negate). A bearish CRT on the original series is the bullish CRT on the
// mirror, so one orientation of the detector serves both directions and the
// two cannot drift apart. Time and ATR are unchanged by the reflection.
func mirror(bars []market.Candle) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, c := range bars {
		out[i] = market.Candle{Time: c.Time, Open: -c.Open, High: -c.Low, Low: -c.High, Close: -c.Close, Volume: c.Volume}
	}
	return out
}

// pivotHighs returns the index of every candle whose high is strictly above the
// n candles on each side. A pivot at p is only knowable once candle p+n has
// closed; callers must require p+n < the candle they are deciding on.
func pivotHighs(bars []market.Candle, n int) []int {
	var out []int
	for p := n; p+n < len(bars); p++ {
		ok := true
		for k := 1; k <= n && ok; k++ {
			if bars[p-k].High >= bars[p].High || bars[p+k].High >= bars[p].High {
				ok = false
			}
		}
		if ok {
			out = append(out, p)
		}
	}
	return out
}

func rangeOf(c market.Candle) float64 { return c.High - c.Low }

// bodyRatio is |close-open| / range; a zero-range candle has no body ratio.
func bodyRatio(c market.Candle) float64 {
	return math.Abs(c.Close-c.Open) / math.Max(rangeOf(c), epsilon)
}

// closeStrength is where the close sits in the candle: 1 at the high, 0 at the
// low (bullish orientation).
func closeStrength(c market.Candle) float64 {
	return (c.Close - c.Low) / math.Max(rangeOf(c), epsilon)
}

// bullishWickRejection is the rejection shape: the lower wick is at least the
// body, the candle closes up and closes in its upper third.
func bullishWickRejection(c market.Candle) bool {
	r := rangeOf(c)
	if r <= 0 {
		return false
	}
	body := math.Abs(c.Close - c.Open)
	lower := math.Min(c.Open, c.Close) - c.Low
	return lower >= body && c.Close > c.Open && c.Close >= c.High-r/3
}
