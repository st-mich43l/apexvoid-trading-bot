// Package liquidity produces objective market facts about liquidity: pools
// anchored at confirmed swings and at equal-level clusters (pool.go,
// equal_high_low.go), each checked for a sweep and, informationally, a
// reclaim (also pool.go). "A liquidity pool exists," "it was swept" —
// never whether those facts form a trade (that is internal/strategy's
// job).
//
// Depends on internal/structure (Pool.Layer reuses structure.StructureLayer,
// and Update consumes structure.StructureState.Swings directly rather than
// recomputing swings — source task §2's core principle) — this is an
// amendment to the dependency order frozen in the architecture task:
// liquidity now sits ABOVE structure, not beside it as a same-rank
// sibling. See docs/architecture/dependency-rules.md's own note on this
// change and test/architecture/dependency_test.go's updated rank table.
//
// Not yet implemented (recorded in docs/analysis-engine-v2-migration.md,
// not silently dropped): session high/low pools, internal/external
// dealing-range liquidity classification (source task §29's remaining
// categories — a genuinely different concept from StructureLayer, see
// Pool's own doc comment).
package liquidity
