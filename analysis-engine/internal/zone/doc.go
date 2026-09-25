// Package zone owns canonical technical geometry: supply/demand zones,
// order blocks, FVG/iFVG, breaker blocks, and flip zones, plus their
// shared lifecycle (Fresh/Touched/PartiallyMitigated/Mitigated/
// Invalidated) and relevance (Immediate/Nearby/Remote/Dormant)
// classification. FVG *detection* belongs here; FVG *trading strategy*
// does not (that is internal/strategy/fvg, a future package reading this
// package's output) — see docs/adr/003-independent-strategy-model.md.
//
// Ported from algo-bot/app/analysis/zones.py and
// technique_geometry.py's not_invalidated/zone_is_spent pair, per
// docs/analysis/zone-v2.md's full specification and Phase S3's own plan
// (apexvoid-bot-prompts/rebuild-strategies.md §14). Three already-shipped
// production fixes are the canonical behavior here, not the pre-fix
// Python this session found and corrected:
//   - PR #574 (6fab6b0): hold-based invalidation — a zone stays valid
//     through a reclaimed sweep, invalidated only by a decisive
//     unreclaimed break or exhausted retest/episode budget. See
//     lifecycle.go.
//   - PR #578 (04db63b): every zone keeps its own per-origin identity —
//     this package never merges overlapping zones into one composite
//     that inherits the earliest member's history. Merging (for display
//     or Confluence Zone scoring) is a higher-layer concern, out of
//     scope here.
//   - PR #579 (a88c9df): any consumer checking whether a zone is still a
//     live barrier uses this package's own NotInvalidated — never a
//     separate, re-derived liveness check.
//
// ZoneWatch (algo-bot/app/autotrade/zone_watch.py) is a DIFFERENT,
// higher-layer concept and is deliberately NOT ported here. It is a
// Redis-persisted, revision-CAS, execution-episode state machine
// (Discovered/WatchingRetest/Evaluating/PublishedLocked/Consumed/
// Invalidated/Expired) that belongs to Algo Bot's future consumption of
// AnalysisOpportunity events (docs/architecture/algo-bot.md), not to
// Analysis Engine's canonical geometry. Do not conflate zone.State (this
// package's own, much simpler lifecycle) with ZoneWatch's state machine.
//
// Dependency rule: zone depends on structure (Swing, StructureBreak,
// DetectDisplacement) and MUST NOT import liquidity, context,
// opportunity, strategy, or transport — see
// docs/architecture/dependency-rules.md's "zone promoted above
// structure, sibling to liquidity" amendment.
package zone
