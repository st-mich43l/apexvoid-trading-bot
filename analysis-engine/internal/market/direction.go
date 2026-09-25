package market

// Direction is a trade/bias direction. A typed enum for the same reason
// Timeframe is (see timeframe.go): an unrecognized direction should be a
// compile- or parse-time error, not a silent string mismatch three layers
// deep in a strategy or risk check.
type Direction string

const (
	Buy  Direction = "BUY"
	Sell Direction = "SELL"
)

// Opposite returns the other direction. Direction values outside
// Buy/Sell (e.g. a zero value) return themselves unchanged — callers that
// care should validate with IsValid first.
func (d Direction) Opposite() Direction {
	switch d {
	case Buy:
		return Sell
	case Sell:
		return Buy
	default:
		return d
	}
}

// IsValid reports whether d is one of the recognized directions.
func (d Direction) IsValid() bool {
	return d == Buy || d == Sell
}
