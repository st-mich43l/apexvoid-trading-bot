# ADR: V6 zone-fill ladder removed

## Status

Accepted. First step of a staged V6 removal (see `adr-trade-plan-v8-cutover.md`
§"Unchanged boundary" — V6 was already dead for autonomous publication as of
that cutover; this removes one of its execution mechanisms from the code
entirely rather than just leaving it unreachable).

## Decision

`ZoneFillPlanner` (the V6 two-leg zone-split ladder builder), the
`ExecutionRoute.ZoneSplit` / `PlannedExecutionRoute.ZoneSplit` execution
routes, `ProcessZoneFillAsync`, `FallBackToSingleEntryAsync`,
`SliceValidSideZone`, `RollbackZoneFillAsync`, and the zone-fill leg-2 TTL
expiry sweep are removed from `ctrader-engine`. A candidate that still
declares `entry_distribution: "zone_split"` is now rejected outright
(`execution policy requires unavailable zone_split limit capability`)
instead of being routed to a ladder that no longer exists.

## Why now, not earlier

`options.ContractMode == "v8_only"` (the production default since the V7-only
cutover, `refactor/v7-only-remove-v6-autonomous-path`, merged 2026-07-28) has
rejected every non-manual candidate before it could ever reach
`ResolveExecutionRoute` — so the zone-split branch, `ProcessZoneFillAsync`,
and the leg-2 TTL sweep had already been fully unreachable in production for
~6 weeks. Zero open positions carried V6 zone-fill state at the time of this
change (confirmed live via Redis before removal). The only thing keeping the
code alive was the C#-test-only bare `legacy_v6` default plus ~20 tests built
specifically to exercise it.

## What did not move

- `AutoTradeOptions`'s `ZoneFill*` fields (`ZoneFillEnabled`, `ZoneFillMinLots`,
  `ZoneFillMinAtr`, `ZoneFillTtlBars`, `ZoneFillFallbackEnabled`) are
  untouched — still resolved from env, still merged from the Python-shared
  runtime manifest, still validated. They are now inert (nothing reads them).
  Removing them requires a coordinated Python + manifest-contract change
  (`AutoTradeConfigHealth`'s cross-service field comparison) and is
  deliberately out of scope here.
- `ZoneFillLegPlan` (now in `EntryLegComment.cs`) survives — the single-limit
  route (`ProcessSingleLimitInitialAsync`, still live for non-manual
  candidates) reuses it purely as a comment-formatting DTO.
- `ClassifyEntryGeometry` / `SelectValidSideProximal` survive — shared with
  the single-limit route.
- `IsBoxRangeScalp`, `IsTrendCandidate`, `IsStrategyMatchCandidate`, the
  `RangeBoxScaleOut*` fields on `AutoTradePositionState`, and their ~70
  associated test fixtures are a separate V6 cluster, not touched here —
  many incidentally exercise sizing/stop/target logic the still-live manual
  `/algo` path also depends on and need per-test triage, not bulk deletion.
- `planned_execution_route()` (Python, `execution_policy.py`) — its own
  docstring already calls it a "legacy route-name helper for parity
  fixtures"; left as-is since it has zero live callers and is exercised only
  by the shared parity fixture.

## Shared fixture

`contracts/autotrade/final-stop-parity.json`'s `routes` section dropped the
one case (`limit_zone_split`) asserting a route that no longer resolves.
