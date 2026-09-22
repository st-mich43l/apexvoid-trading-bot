package structure_test

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// c is a compact candle builder shared by every test in this package.
func c(t int64, o, h, l, cl float64) market.Candle {
	return market.Candle{Time: t, Open: o, High: h, Low: l, Close: cl, Volume: 1}
}

// flatATR returns a constant-value ATR series the length of n candles —
// most tests want a simple, known normalization constant rather than a
// real computed series (indicator.CanonicalATR is exercised separately in
// test/indicator and test/engine's integration test).
func flatATR(n int, value float64) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = value
	}
	return out
}
