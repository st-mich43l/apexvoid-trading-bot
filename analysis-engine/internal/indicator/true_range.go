// Package indicator ports app/analysis/math_utils.py and
// app/analysis/indicators.py. See docs/go-analysis-migration-audit.md §2.1:
// TWO different ATR formulas are live in Python today (a simple rolling
// mean, and pandas_ta's Wilder/RMA smoothing), used by different callers,
// and they diverge materially (~6.7% apart measured on real XAU M5 bars).
// This package ports BOTH, clearly named, rather than picking a "correct"
// one — which one is canonical is a product decision for the owner, not
// something to resolve silently during translation (§17).
package indicator

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// TrueRange computes the true range series for candles, oldest-first,
// exactly matching app/analysis/math_utils.py::true_range:
//
//	tr[i] = max(high[i]-low[i], |high[i]-close[i-1]|, |low[i]-close[i-1]|)
//
// with tr[0] = high[0]-low[0] (no previous close — pandas' `.shift(1)`
// produces NaN there, and `pd.concat(...).max(axis=1)` with a NaN operand
// takes the max of the remaining, non-NaN values, which for row 0 is just
// high[0]-low[0]).
func TrueRange(candles []market.Candle) []float64 {
	out := make([]float64, len(candles))
	for i, c := range candles {
		hl := c.High - c.Low
		if i == 0 {
			out[i] = hl
			continue
		}
		prevClose := candles[i-1].Close
		hc := absf(c.High - prevClose)
		lc := absf(c.Low - prevClose)
		out[i] = maxf(hl, maxf(hc, lc))
	}
	return out
}

func absf(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func maxf(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
