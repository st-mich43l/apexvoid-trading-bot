// Package confluence is the deliberate compositional exception to the
// independent-strategy model (docs/adr/003-independent-strategy-model.md).
// It may combine independent technical evidence from multiple strategies'
// opportunity.Candidate output — demand + order block + FVG + Fibonacci +
// liquidity + flip zone — into a combined confluence score. No other
// strategy inherits from this package as a generic base; it is a
// consumer of internal/opportunity output, not a base class for
// internal/strategy implementations.
//
// Proposed files (source task §23, not yet implemented beyond score.go):
// zone.go, overlap.go, evidence.go.
package confluence
