package market

import (
	"fmt"
	"math"
)

// Geometry is one instrument's price/pip geometry — everything downstream
// math needs to turn a raw price difference into pips, or round a price to
// the instrument's own tick size. Built once per symbol from the resolved
// runtime manifest (see internal/config), never hardcoded — §12 is explicit
// that an XAU assumption (e.g. `pipSize := 0.1`) must never leak into FX,
// and an unknown instrument must fail closed rather than default to one.
type Geometry struct {
	Symbol       string
	BrokerSymbol string
	PipSize      float64
	PriceDigits  int
}

// NewGeometry validates and constructs a Geometry. A non-positive pip size
// or negative digit count is a fail-closed configuration error, not a
// value to silently clamp — a corrupt manifest must stop the instrument,
// not run it on made-up geometry.
func NewGeometry(symbol, brokerSymbol string, pipSize float64, priceDigits int) (Geometry, error) {
	if symbol == "" {
		return Geometry{}, fmt.Errorf("market: geometry requires a symbol")
	}
	if pipSize <= 0 {
		return Geometry{}, fmt.Errorf("market: %s has non-positive pip size %v", symbol, pipSize)
	}
	if priceDigits < 0 {
		return Geometry{}, fmt.Errorf("market: %s has negative price digits %d", symbol, priceDigits)
	}
	return Geometry{
		Symbol:       symbol,
		BrokerSymbol: brokerSymbol,
		PipSize:      pipSize,
		PriceDigits:  priceDigits,
	}, nil
}

// RoundToTick rounds price to the instrument's own tick size
// (10^-PriceDigits), matching the rounding granularity the .NET executor
// and Python's `_round_price` both key off of the manifest's
// `price_digits` for.
func (g Geometry) RoundToTick(price float64) float64 {
	scale := 1.0
	for i := 0; i < g.PriceDigits; i++ {
		scale *= 10
	}
	return roundHalfAwayFromZero(price*scale) / scale
}

// FloorToTick returns the greatest instrument tick not above price. It is
// used for prices where rounding upward would make the technical fact less
// protective (for example a BUY stop or a BUY target).
func (g Geometry) FloorToTick(price float64) float64 {
	scale := g.tickScale()
	return math.Floor(normalizeScaled(price*scale)) / scale
}

// CeilToTick returns the smallest instrument tick not below price. It is used
// for prices where rounding downward would make the technical fact less
// protective (for example a SELL stop or a SELL target).
func (g Geometry) CeilToTick(price float64) float64 {
	scale := g.tickScale()
	return math.Ceil(normalizeScaled(price*scale)) / scale
}

func (g Geometry) tickScale() float64 {
	scale := 1.0
	for i := 0; i < g.PriceDigits; i++ {
		scale *= 10
	}
	return scale
}

// normalizeScaled removes the tiny binary floating-point residue that would
// otherwise turn an already aligned price into the adjacent tick when using
// FloorToTick/CeilToTick (for example 4132.61*100).
func normalizeScaled(scaled float64) float64 {
	nearest := math.Round(scaled)
	if math.Abs(scaled-nearest) < 1e-9 {
		return nearest
	}
	return scaled
}

func roundHalfAwayFromZero(v float64) float64 {
	if v >= 0 {
		return float64(int64(v + 0.5))
	}
	return float64(int64(v - 0.5))
}

// PipsBetween converts a raw price distance into pips using this
// instrument's own pip size — the one place a price-difference-to-pips
// conversion should happen, instead of every caller dividing by a literal.
func (g Geometry) PipsBetween(a, b float64) float64 {
	d := a - b
	if d < 0 {
		d = -d
	}
	return d / g.PipSize
}
