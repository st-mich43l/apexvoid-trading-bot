// Package structure owns market structure as its own first-class domain:
// raw pivots, validated swings, structural hierarchy (major/intermediate/
// internal/micro — see docs/architecture/analysis-engine.md; a strategy
// must be able to read "micro bullish structure" vs. "major bearish
// structure" without recomputing either), HH/HL/LH/LL classification,
// BOS/CHoCH, protected highs/lows, and break classification (wick/close/
// displacement/sweep).
//
// It answers "what happened structurally," never "should we trade it" —
// that is internal/strategy's job, reading this package's output through
// internal/context, never recomputing it.
//
// Ports (see docs/go-analysis-migration-audit.md, computation table):
// swings.find_swings -> structure/swing.go; structure.market_structure ->
// structure/hierarchy.go (or market_structure.go); structure.structure_breaks
// -> structure/break.go; levels.key_levels -> structure/level.go. Not yet
// implemented — this package is a boundary reservation only, per this
// architecture task's own restraint principle (do not port swing math as
// part of an architecture-definition task).
//
// Dependency rule: structure MUST NOT import internal/opportunity or
// internal/strategy (docs/architecture/dependency-rules.md).
package structure
