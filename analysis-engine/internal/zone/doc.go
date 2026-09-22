// Package zone owns shared technical geometry: supply/demand, order
// blocks, FVG/iFVG, breaker blocks, flip zones, and mitigation/lifecycle
// state. FVG *detection* belongs here; FVG *trading strategy* does not
// (that is internal/strategy/fvg, a future package, reading this
// package's output).
//
// Ports (docs/go-analysis-migration-audit.md computation table):
// zones.displacement/supply_demand/order_blocks/fvg/flip_zones/
// breaker_blocks/mark_mitigation/merge_zones/score_zones/
// reconcile_opposing -> zone/{displacement,supply,demand,order_block,fvg,
// ifvg,breaker,flip,mitigation}.go. Not yet implemented — boundary
// reservation only. This is also where this session's live production
// fixes need to land as the canonical Go behavior once ported, not a
// "port the old bug" translation: PR #574 (hold-based invalidation with
// sweep-and-reclaim forgiveness), PR #578 (per-origin zone identity
// instead of merge-composite history), PR #579 (opposing-wall liveness
// using the same hold rule).
//
// Dependency rule: zone MUST NOT import internal/strategy.
package zone
