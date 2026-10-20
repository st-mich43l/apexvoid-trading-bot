package market

import "math"

// ValidSeries is true when the candles are finite, internally consistent (high is the
// highest price of the candle, low the lowest) and strictly increasing in time. A
// malformed or duplicated series is not a market history: nothing is decided from it.
func ValidSeries(bars []Candle) bool {
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

// Mirror reflects prices about zero (open and close negate, high and low swap and
// negate). A bearish setup on the original series is the bullish one on the mirror, so
// one orientation of a detector serves both directions and the two cannot drift apart.
// Time, volume and every ATR multiple are unchanged by the reflection.
func Mirror(bars []Candle) []Candle {
	out := make([]Candle, len(bars))
	for i, c := range bars {
		out[i] = Candle{Time: c.Time, Open: -c.Open, High: -c.Low, Low: -c.High, Close: -c.Close, Volume: c.Volume}
	}
	return out
}
