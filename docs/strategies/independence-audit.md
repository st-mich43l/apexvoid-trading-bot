# Strategy independence audit

Principle: 21 strategies, 21 independent technical decisions. One shared
market-intelligence layer, one opportunity lifecycle, one execution authority. No
strategy families.

This audit lists every place a strategy's behavior was decided by a group it
belonged to, what that did to independence, and what was done. Line references are to
the code before this change (`39272967`).

## Result

| | Before | After |
|---|---|---|
| A technique's candidate removed by a Confluence Zone overlap | yes (`technique_decision.go`) | no |
| Execution policy resolved through a family | yes (`execution_policy.py`, 9 family policies) | no: one catalog row per strategy |
| Python behavior flags derived from a family | yes (`strategy_names.py`, `strategy_taxonomy.py`) | no: per-strategy flags |
| Go stop envelope chosen by group sets | yes (`stop_envelope.go`) | no: declared per strategy, all 21 required |
| Confirmation contract inferred from a family string | yes (`execution_confirmation.py`) | no: per-strategy field |
| Trading decision reads the `strategy_family` label | several | none outside serialisation (test-enforced) |

## Findings and disposition

### Removed

| ID | Source | Dependency | Effect on independence | Disposition |
|---|---|---|---|---|
| G1 | `analysis-engine/internal/strategyutil/technique_decision.go` `Technique()` | skipped every instance a confluence band covered | **Violation.** A valid FVG / Order Block / Supply / Demand / iFVG / CRT candidate never existed whenever Confluence Zone overlapped it, so the lifecycle never saw it and arbitration could not weigh it. | Removed. `Technique()` reads only the technique's own instances. The frozen rule survives as `TechniqueExcludingConfluenceCoverage`, used only by `test/techniqueparity`. |
| G2 | `analysis-engine/internal/engine/stop_envelope.go` | three group sets (`reactionFamilyStrategies`, `m1ScalpStopStrategies`, `rangeRoomSyncedStrategies`) plus an implicit default | A new strategy silently inherited the trend envelope. | `strategyStopEnvelope`: every one of the 21 IDs declares its own kind; a test fails on a missing or extra ID. Values unchanged. |
| P1 | `algo-bot/app/autotrade/execution_policy.py` `_STRATEGY_FAMILY`, `_DEFAULT_POLICIES`, `_FAMILY_HARD_DRIFT_DEFAULT`, `_family_min/hard_drift_pips`, `classify_tier`, `policy_for` | thirty strategy names mapped to nine family policies; entry drift, minimum RR, zone width, target room, order type, regimes, hard drift caps and the range risk ceiling all inherited | Retuning one strategy's execution tolerance changed every strategy in its family (all six techniques, Flip Zone and the retired zone names shared one policy; the four M1 scalps shared Range Edge's). | `strategy_catalog.py`: one row per strategy with literal values; `policy_for` reads only the strategy's own row. |
| P2 | `strategy_names.py` (`family`, `CANONICAL_FAMILY_*`, `names_for_family`), `strategy_registry.py` (`detector_family`, `execution_family`, `canonical_family`, archetypes), `strategy_taxonomy.py` (`canonical_family`, `family=` parameters) | membership sets derived from a family | Behavior flags (own-room bypass, market-scale routing, technique/zone) were inherited. | Registry deleted; flags are per-strategy booleans on the catalog row; `family=` parameters removed. |
| P3 | `execution_route.py` `REACTION_MARKET_SCALE_FAMILIES` | routing decided by a family string | Key Level / Session Level / Trendline routing keyed on a group. | Keyed on the strategy only. |
| P4 | `execution_confirmation.py` `_ZONE_CONFIRMATION_FAMILIES`, `_M5_AUTHORITATIVE_*_FAMILIES`, `_REACTION_STRATEGIES`, `family == "momentum_continuation"` | confirmation contract inferred from the family string | Legacy-detector path only (Go-origin matches return first), but a mislabelled match changed its contract. | Per-strategy `confirmation` field; family-only branches removed. |
| P5 | `reaction_funnel.py` `funnel_bucket(family=)`, `bump_funnel`, `stats_ingestion.py` | stats bucket decided by `strategy_family` of the event | A strategy's funnel bucket depended on an event label. | `family` parameter removed; bucket from the strategy. |
| P6 | `worker.py` `_strategy_group_id` | `match.family == "mapped_zone"` | Dead for Go matches (their label is `mapped_zone_reaction`); a latent group-id path. | Removed; `strategy_mode` decides. |
| C1 | `ctrader-engine/src/TradePlanRuntime.cs` `ScalpFamilies` in `IsScalpPlan` | scalp status from the plan's family label | Redundant with the strategy list for every real strategy; wrong for any plan carrying a mismatched label. | Decided by the plan's strategy only. |

### Record-compatibility labels kept (no decision reads them)

`strategy_family` stays in TradePlan V8, lifecycle/`setup_status` events, persisted
rows and the cTrader executor's echoed events, because those are historical records
and an existing wire contract (`contracts/autotrade/trade-plan-v8.json`, required by
`TradePlan.cs`). It is now `StrategyProfile.legacy_family_label`, written once in
`go_opportunity_policy.py` and echoed. Two identity hashes also take it as an input
(`strategy_identity.thesis_id`, `reaction_identity.mapped_group_id`); changing those
bytes would orphan live thesis claims and group ids at deployment, so they are
unchanged. Within a thesis the structural id is already per strategy
(`<catalog_id>:<bucket>` or the strategy's own reaction zone id), so the label
cannot merge two strategies. `test_no_trading_decision_reads_the_legacy_family_label`
fails if any comparison, membership test or lookup on it appears outside the listed
serialising files.

`strategy_family="manual"` is a plan *kind* (owner-armed manual `/algo`), not a
strategy family; it keeps its name on the wire.

### Remaining coupling (not a family, documented and pinned)

| ID | Source | Coupling | Why it is left | Next step |
|---|---|---|---|---|
| R1 | `config/auto-algo.yml` `strategies.scalping.mode` | one `live` switch enables `range_sweep`, `scalp_breakout_retest` and `impulse_pullback` together on the Python side (Go enable/observe-only is already per strategy) | A per-strategy key is a configuration change; the instruction was not to change production configuration. `test_enable_switches_are_per_strategy_except_the_documented_scalp_mode` pins the exact set so no new shared switch can appear. | Add `strategies.scalping.<id>.enabled` defaulting to the current mode, then delete the shared read. |
| R2 | `config/instruments.yml` / `auto-algo.yml` `execution.range`, `execution.trend`, `execution.mapped_zone` | strategies name one section for drift tolerance, minimum pips and hard caps (e.g. all six techniques and Key Level read `mapped_zone`) | Instrument-owned policy sections, not strategy decisions: each strategy declares which section it reads (`drift_section`), and nothing infers it. Retuning a section is deliberately instrument-wide. | Per-strategy overrides under the instrument if a measured need appears. |
| R3 | `config.go` `StopEnvelopeConfig` (reaction / scalp / range / trend bundles) | four configured stop-distance bundles | Same: configured policy, selected per strategy by `strategyStopEnvelope`. | As R2. |
| L1 | `TradePlanExecutionEngine.cs` `DegradeScalpLadderForMinVolume` (`StrategyFamily != "scalp"`) and `trade_plan_builder.py` `is_scalp_plan` (`match.family == "scalp"`) | Go plans never carry the label `scalp` (their label is `range_reversion`), so the min-volume ladder degrade and the scalp `risk_percent`/`sizing_mode` branches are unreachable for autonomous plans. | Making them strategy-driven would change scalp sizing and ladders on small accounts - an exposure change. | Owner decision: either enable them for the three M1 scalps (with a replay of sizing impact) or delete the dead branches. |

## Quality comparability across strategies (arbitration input)

Independent detection now sends more candidates into the same ranking, so what
`quality.overall` means per strategy matters. `TestQualityScoresAcrossStrategiesAreMeasuredNotAssumed`
measures it on all six committed production captures (confirmed candidates only,
pooled; XAU, EURUSD, GBPUSD, GBPJPY, USDJPY).

**Confirmed (measured, not a performance claim):**

- `quality.overall` is `clamp01(stars / 3)` for the technique and level strategies
  (`strategyutil/technique_candidate.go`, `keylevel/keylevel.go`). With the minimum of
  two stars it can only be **0.67 or 1.00**: eleven of fourteen strategies that produced
  confirmed candidates take exactly two values (`trendline` one). For them quality is
  the star count again, not an independent measure.
- `range_edge` (24 distinct values, median 0.85), `snap_back` (25, median 0.76) and
  `fade_scalp` (4, median 0.65) use their own continuous scales.
- Of 2,786 competing groups (two or more strategies, same symbol, direction, closed bar
  and entry corridor with a 1 ATR pad), **47 %** tie at the top quality and are decided
  by confluence, structural quality, freshness and finally the intent id. Of the 1,480
  decided on quality alone, 265 went to a continuous-scale strategy (`range_edge` 219),
  and 119 (8 %) would change winner if each score were first normalised inside its own
  strategy.

**What it means:** a two-star zone or level setup can never outrank a typical
`range_edge` setup (0.85) however good it is, and half the contests are settled by a
tie-break that carries no technical meaning. **What it does not show:** that the
continuous strategies are better or worse. No realized outcome data exists for the
captures, so whether the ordering helps or hurts expectancy is unproven.

**Disposition:** HYPOTHESIS upgraded to a CONFIRMED_MECHANISM (P2). No change made; the
rank order is an execution-policy decision. Proposal: calibrate each strategy's score
to a common scale only after `realized_pips` (#750) has accumulated enough closed
trades per strategy to fit it against outcomes, and keep any change behind
out-of-sample replay.

## Preserved shared primitives

Zone building and clipping (`techniquezone`), structure, liquidity, indicators, ATR /
MAD, `strategyutil` detector helpers, ladder and stop mathematics, the execution
route builders and `entry_overlap`. These take inputs and return facts; none decides
for a strategy.

## Deliberate departures from Python parity

Only G1. The oracle golden and its generator are untouched. See
[the strategies README](README.md#deliberate-departure-from-the-frozen-python-oracle)
for the measured effect (+124 opportunities on the XAU M1 production capture, 1,663 to 1,787).

## Unchanged execution and risk invariants

Equity-table sizing, the XAU ladder and 40/20/20/20 closes, FX single entry and 1R/2R,
break-even rules, opposite-exposure policy, same-direction stacking, the
45-minute / 1-ATR entry corridor (which still lets exactly one of several
coinciding strategies trade), the Go-side same-thesis arbitration and the
TradePlan V8 contract. `test_removing_families_did_not_change_any_strategys_execution_decisions`
compares policy values, 120 tier combinations, 12 drift cases and every behavior flag
for 60 strategy names and aliases against output captured from the family-era code.
