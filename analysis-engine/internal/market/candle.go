// Package market holds the core OHLC domain types the rest of the analysis
// engine is built on: Candle, the bounded CandleWindow ring buffer, and
// per-instrument geometry. See docs/go-analysis-migration-audit.md for the
// Python modules these mirror and why.
package market

// Candle is one closed OHLC bar. Time is a Unix second timestamp (the same
// unit `bars:{SYMBOL}:{TF}` scores use in Redis today — see
// app/analysis/ohlc_source.py), not a time.Time, so a CandleWindow can stay
// a flat, allocation-free slice of value types.
type Candle struct {
	Time   int64
	Open   float64
	High   float64
	Low    float64
	Close  float64
	Volume float64
}

// IsBullish/IsBearish mirror app/analysis/math_utils.py::candle_direction —
// a doji (Close == Open) is neither.
func (c Candle) IsBullish() bool { return c.Close > c.Open }
func (c Candle) IsBearish() bool { return c.Close < c.Open }

// Range is the candle's high-low span. Zero or negative on a zero-range /
// malformed bar — callers must check before dividing by it (see
// candle_geometry.go, which mirrors app/analysis/candle_geometry.py's own
// explicit zero-range convention rather than guessing one).
func (c Candle) Range() float64 { return c.High - c.Low }

// Body is the absolute open/close distance.
func (c Candle) Body() float64 {
	d := c.Close - c.Open
	if d < 0 {
		return -d
	}
	return d
}
