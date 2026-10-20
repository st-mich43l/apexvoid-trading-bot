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

func newATRWindow(bars []market.Candle, length, window int) *indicator.WilderWindow {
	return indicator.NewWilderWindow(bars, length, window)
}

func validSeries(bars []market.Candle) bool { return market.ValidSeries(bars) }

// firstAtOrAfter is the index of the first candle whose open time is >= t, or
// len(bars) when none is.
func firstAtOrAfter(bars []market.Candle, t int64) int {
	return sort.Search(len(bars), func(i int) bool { return bars[i].Time >= t })
}

// mirror reflects prices about zero (see market.Mirror).
func mirror(bars []market.Candle) []market.Candle { return market.Mirror(bars) }

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
func bodyRatio(c market.Candle) float64 { return c.BodyRatio() }

// closeStrength is where the close sits in the candle: 1 at the high, 0 at the
// low (bullish orientation).
func closeStrength(c market.Candle) float64 { return c.CloseLocation() }

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
