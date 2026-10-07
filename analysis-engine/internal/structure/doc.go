// Package structure owns market structure as its own first-class domain:
// raw pivots (pivot.go), promoted/confirmed swings with a significance
// score (swing.go), the explicit structural hierarchy (Micro/Internal/
// Intermediate/Major — swing.go, hierarchy.go), HH/HL/LH/LL classification
// with an ATR-normalized equality band (classifier.go), a reusable
// multi-candle displacement model (displacement.go), and break
// classification distinguishing a mere wick, a held close, a
// displacement-confirmed break, a swept-and-reclaimed level, and a
// held-then-quickly-reversed failed break (break.go), plus the BOS/CHoCH
// classification built on top of that (event.go). Settings (config.go)
// and Update/StructureState/Book (state.go, book.go) tie it together into
// one per-timeframe entrypoint.
//
// Every threshold's provenance and every deliberate divergence from the
// legacy Python system are documented inline, file by file, in this
// package's own doc comments and proven by its tests — source task §65's
// own requirement that documentation and code always describe the same
// behavior.
//
// It answers "what happened structurally," never "should we trade it" —
// that is internal/strategy's job, reading this package's output through
// internal/context, never recomputing it.
//
// Dependency rule: structure MUST NOT import internal/opportunity or
// internal/strategy (docs/architecture.md).
package structure
