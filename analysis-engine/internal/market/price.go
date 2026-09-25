package market

import "math"

// Price is a raw instrument price, typed for the same reason Direction/
// Timeframe/Symbol are — so a Structure V2 function signature reads
// unambiguously (Pivot.Price market.Price, not a bare float64 that could
// be an ATR value or a pip count by accident). Arithmetic works directly
// since the underlying type is float64 (a - b, a > b, ... all just work).
type Price float64

// IsFinite reports whether p is a real, usable price — never NaN or Inf.
// Every structure/liquidity algorithm must reject non-finite input at the
// boundary (docs/analysis/market-structure-v2.md's validation section)
// rather than let it propagate into a Swing/Break/Pool silently.
func (p Price) IsFinite() bool {
	f := float64(p)
	return !math.IsNaN(f) && !math.IsInf(f, 0)
}

// Sub returns the signed distance p - other.
func (p Price) Sub(other Price) float64 { return float64(p - other) }

// Distance returns the absolute distance between p and other.
func (p Price) Distance(other Price) float64 {
	d := float64(p - other)
	if d < 0 {
		return -d
	}
	return d
}

// PriceLevel is a single labeled price — the market-domain building block
// for anything that needs "one price, and why it matters" without pulling
// in a whole Zone's lifecycle/mitigation state (that richer concept is
// internal/zone's, a higher layer; see docs/architecture/analysis-engine.md
// on why opportunity.Candidate's Entry field is its own small EntryZone
// type rather than reusing internal/zone's Zone).
type PriceLevel struct {
	Price Price
	Label string // e.g. "structural_swing_low", "sweep_extreme" — free-form, not an enum yet
}
