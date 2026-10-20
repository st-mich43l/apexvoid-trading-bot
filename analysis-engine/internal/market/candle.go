// Package market holds the core OHLC domain types the rest of the analysis
// engine is built on: Candle, the bounded CandleWindow ring buffer, and
// per-instrument geometry. These types are independent of service adapters so
// the same calculations work in live processing and replay.
package market

// Candle is one closed OHLC bar. Time is a Unix second timestamp (the same
// unit `bars:{SYMBOL}:{TF}` scores use in Redis today — see
// Redis), not a time.Time, so a CandleWindow can stay
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

// The ratios below are fractions of the candle's range. A zero-range (or
// malformed) candle has no geometry, so each returns 0 instead of dividing by it.

// BodyRatio is |close-open| / (high-low).
func (c Candle) BodyRatio() float64 {
	if r := c.Range(); r > 0 {
		return c.Body() / r
	}
	return 0
}

// CloseLocation is where the close sits in the range: 1 at the high, 0 at the low.
func (c Candle) CloseLocation() float64 {
	if r := c.Range(); r > 0 {
		return (c.Close - c.Low) / r
	}
	return 0
}

// UpperWickRatio is (high - max(open, close)) / (high - low).
func (c Candle) UpperWickRatio() float64 {
	if r := c.Range(); r > 0 {
		return (c.High - maxPrice(c.Open, c.Close)) / r
	}
	return 0
}

// LowerWickRatio is (min(open, close) - low) / (high - low).
func (c Candle) LowerWickRatio() float64 {
	if r := c.Range(); r > 0 {
		return (minPrice(c.Open, c.Close) - c.Low) / r
	}
	return 0
}

func maxPrice(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func minPrice(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
