package market

// PriceLevel is a single labeled price — the market-domain building block
// for anything that needs "one price, and why it matters" without pulling
// in a whole Zone's lifecycle/mitigation state (that richer concept is
// internal/zone's, a higher layer; see docs/architecture/analysis-engine.md
// on why opportunity.Candidate's Entry field is its own small EntryZone
// type rather than reusing internal/zone's Zone).
type PriceLevel struct {
	Price float64
	Label string // e.g. "structural_swing_low", "sweep_extreme" — free-form, not an enum yet
}
