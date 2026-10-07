// Package zone owns canonical technical geometry: supply/demand zones,
// order blocks, FVG/iFVG, breaker blocks, and flip zones, plus their
// shared lifecycle (Fresh/Touched/PartiallyMitigated/Mitigated/
// Invalidated) and relevance (Immediate/Nearby/Remote/Dormant)
// classification. FVG *detection* belongs here; FVG *trading strategy*
// does not (that is internal/strategy/fvg, a package reading this output) —
// see docs/architecture.md.
//
// Ported from algo-bot/app/analysis/zones.py and
// technique_geometry.py's not_invalidated/zone_is_spent pair, per
// The zone domain owns technique geometry and lifecycle.
// Three already-shipped production fixes are the canonical behavior
// here, not the pre-fix
// Python this session found and corrected:
//   - hold-based invalidation — a zone stays valid
//     through a reclaimed sweep, invalidated only by a decisive
//     unreclaimed break or exhausted retest/episode budget. See
//     lifecycle.go.
//   - every zone keeps its own per-origin identity —
//     this package never merges overlapping zones into one composite
//     that inherits the earliest member's history. Merging (for display
//     or Confluence Zone scoring) is a higher-layer concern, out of
//     scope here.
//   - any consumer checking whether a zone is still a
//     live barrier uses this package's own NotInvalidated — never a
//     separate, re-derived liveness check.
//
// Dependency rule: zone depends on structure (Swing, StructureBreak,
// DetectDisplacement) and MUST NOT import liquidity, context,
// opportunity, strategy, or transport — see
// docs/architecture.md's "zone promoted above
// structure, sibling to liquidity" amendment.
package zone
