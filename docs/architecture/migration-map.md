# Migration Map

Every current top-level module mapped to: current responsibility, target
service/package, action, and rough sequencing. This is a planning record,
not a task list with dates — "when" is relative to the staged sequence
already established by `go-analysis-migration-audit.md` (Stages 1–8) and
this task's own next step (the market-structure specification).

Legend for **Action**: `keep` (stays as-is, already correct) ·
`port` (translate to Go, parity-tested) · `reorganize` (move within Python,
logic unchanged) · `rewrite` (new logic, not a 1:1 port) ·
`delete-after-cutover` (duplicate path, removed once its replacement is
proven) · `no-action` (out of scope / research tooling).

## `analysis-engine/` (Go) — already in progress

| Current path | Current responsibility | Target | Action | When |
|---|---|---|---|---|
| `internal/market/*` | Candle, CandleWindow, Timeframe, Symbol, Geometry | same | keep | done |
| `internal/indicator/*` | TrueRange, SimpleATR, WilderATR | same | keep | done |
| `internal/config/*` | Configuration V3 direct reader | same | keep | done |
| `internal/{structure,liquidity,zone,context,opportunity,strategy,confluence,state,engine,transport,visualization,telemetry}/*` | (did not exist) | same | scaffolded this task (doc.go + minimal types) | this task |

## `algo-bot/app/analysis/*` → `analysis-engine`

| Current path | Current responsibility | Target service/package | Action | When |
|---|---|---|---|---|
| `math_utils.py` (`atr_series`, `true_range`, `atr_at`, `atr_scalar`) | Simple-mean ATR (canonical, 19 call sites) | `internal/indicator` | port | done (both formulas ported side by side; canonicalization decision open — V1) |
| `indicators.py` (`atr`) | Wilder RMA ATR | `internal/indicator` | port | done, canonicalization open |
| `swings.py` | Hybrid fractal+zigzag swings, causal mode | `internal/structure` | port | Stage 2 (next) |
| `structure.py::market_structure`, `structure_breaks` | Bias / BOS/CHoCH — canonical, single-owner | `internal/structure` | port | Stage 2 |
| `structure.py` (everything else: `swings()`, `key_levels()`, `order_blocks()`, `fvg()`, `flip_zones()`, `entry_zone()`, `find_retest()`, `equal_highs_lows()`) | Duplicate re-derivation of `zones.py` primitives, hardcoded ATR length | — | **delete-after-cutover**, not ported | after Stage 2 proves `TimeframeState` reachable from detector fallback paths |
| `zones.py` (`displacement`, `supply_demand`, `order_blocks`, `fvg`, `flip_zones`, `breaker_blocks`, `mark_mitigation`, `merge_zones`, `score_zones`, `reconcile_opposing`) | Core zone primitives, mitigation-stamped (hold-based validity as of PR #578/#579 this session) | `internal/zone` | port | Stage 2 |
| `levels.py` (`key_levels`) | Clustering + wick-touch key levels | `internal/structure` | port | Stage 2 |
| `liquidity.py` (`liquidity_pools`, `liquidity_grabs`) | Liquidity pools/sweeps | `internal/liquidity` | port | Stage 3 |
| `fibonacci.py` | Fib ladder/nearest | `internal/technique` (or folded into `zone`) | port | Stage 3 |
| `momentum.py` | Momentum state | `internal/indicator` | port | Stage 3 |
| `dealing_range.py` | Dealing range | `internal/context` (regime sub-package) | port | Stage 3 |
| `regime.py` | Box-break / displacement grade | `internal/context` | port | Stage 3 |
| `trendlines.py` + `trendline_v2.py` | v1/v2 trendline dispatch (single entrypoint) | `internal/context` (trendline sub-package) | port, both versions | Stage 3/4, large surface, not yet read function-by-function |
| `scalp_ranges.py` | Scalp structure/ranges (barriers, range) | `internal/context` (scalp sub-package) | port | Stage 3 |
| `technique_geometry.py`, `technique_detectors.py` | Technique instances (S/D, OB, FVG/iFVG, CRT) + publishing — includes this session's hold-based validity, unmerged-zone-origin, opposing-wall-liveness fixes (PRs #578, #579) | `internal/zone` + `internal/strategy` | port geometry to `zone`; **rewrite** detection as independent strategies (reject the generic family) | Stage 6, after market-structure spec |
| `detectors.py` (`LIVE_DETECTOR_REGISTRY`, all detector functions) | All strategy/detector logic | `internal/strategy/<name>` | **rewrite**, not ported 1:1 | Stage 6+, after market-structure spec is approved |
| `mad_phase.py` | MAD phase classification + Redis-mixed scoring/gating | `internal/mad` (pure) + Redis I/O at the service boundary | port (pure) + split (I/O) | Stage 4, explicitly flagged risky (soft confluence, must never become a hard gate) |
| `candle_geometry.py`, `candle_displacement.py`, `candle_evidence.py`, `candle_rejection.py`, `candle_sequences.py` | Candle pattern evidence (V2) | `market/candle.go` + technique-family evidence packages | port | Stage 6, large surface deferred |
| `engine.py::analyze()`/`_analyze_tf()` | The canonical per-timeframe pipeline orchestrator | `internal/engine` | port (as the reference shape for `SymbolState` construction) | Stage 5 |
| `engine.py::scalp_structure()` | Second, lighter recompute — **no live callers found** | — | confirm dead with owner, then delete; not ported | Stage 2 cleanup |
| `bar_event_dispatcher.py`, `ohlc_source.py` | Bar event normalization, OHLC windowing | `internal/marketdata` | port | Stage 1 continuation |
| `market_map.py`, `market_map_delivery.py` | Owner-facing map rendering/delivery | stays `algo-bot` (`journal`/`telegram`, display-only, not a computation source) | keep, reorganize | after cutover |
| `actionability.py`, `execution_eligibility.py`, `entry_location.py`, `key_level_role.py`, `confluence_zone.py`, `session_liquidity.py`, `structural_reaction_support.py`, `m1_trigger.py` | Detector-adjacent decision/gating logic, not pure computation | Needs individual classification at Stage 6 (some may be `strategy`, some `algo-bot/risk`) | unclassified — flagged, not decided | Stage 6 |

## `algo-bot/app/scalping/*` — the largest open item

| Current path | Current responsibility | Target | Action | When |
|---|---|---|---|---|
| `context.py` (`_swings_from_ohlc`, `build_scalp_context`) | Independent analysis stack: own swings, own dealing range | `internal/structure` (if unified) or a documented distinct Go variant | owner decision required | Stage 6 |
| `strategies.py`, `math_strategies.py`, `activation.py` | Independent orchestration: scalp-specific strategies, activation | `internal/strategy/<scalp-name>` (analysis) + `algo-bot/auto_algo` (orchestration) — split, not moved whole | rewrite | Stage 6 |
| `risk.py`, `rollout.py`, `lifecycle.py` | Scalp-specific risk/lifecycle | `algo-bot/risk`, `algo-bot/auto_algo` | reorganize | after Stage 6 split |
| `lab_event_builder.py`, `replay.py`, `replay_lab.py`, `mad_replay.py`, `performance.py`, `research_stamp.py` | Offline/research tooling | stays Python (§28: research plane) | no-action | — |
| `microstructure.py`, `math_features.py`, `unified_context.py`, `models.py`, `outcomes.py`, `ranking.py`, `telemetry.py`, `publish.py` | Mixed analysis/orchestration/telemetry | Split per above once Stage 6 decision is made | unclassified | Stage 6 |

## `algo-bot/app/autotrade/*` → `algo-bot` target tree (reorganize, logic mostly kept)

| Current path | Target package | Action |
|---|---|---|
| `zone_watch.py`, `zone_execution_cutover.py`, `zone_execution_runtime.py`, `zone_relevance.py`, `setup_lifecycle.py`, `setup_expiry_sweeper.py` | `auto_algo/lifecycle.py`, `auto_algo/state.py` | reorganize |
| `entry_activation.py`, `execution_confirmation.py`, `killzone.py`, `range_context.py`, `range_lifecycle.py`, `range_targets.py`, `trend.py`, `map_strategy.py` | `auto_algo/eligibility.py`, `auto_algo/policy.py` | reorganize (consumers of analysis output — no computation, per audit §2.7) |
| `active_exposure.py`, `arbitration.py` | `risk/exposure.py`, `risk/policy.py` | reorganize |
| `structural_barriers.py`, `structural_target_room.py`, `protective_stop.py`, `entry_distance.py`, `scale_context.py`, `scale_in_sizing.py`, `volume_pips.py`, `units.py` | `risk/sizing.py`, `risk/limits.py` | reorganize |
| `strategy_match.py`, `strategy_match_ready.py`, `strategy_names.py`, `strategy_registry.py`, `strategy_taxonomy.py` | `auto_algo/eligibility.py` (single-table registry, already the pattern §22 asks for) | keep pattern, reorganize location |
| `trade_plan.py`, `trade_plan_builder.py`, `trade_plan_stream.py`, `execution_policy.py`, `execution_route.py`, `route_outcome.py`, `event_integrity.py`, `candidate_execution_state.py`, `candidate_publish.py`, `direct_publish_same_cycle.py` | `execution/publisher.py`, `execution/consumer.py`, `execution/reconciliation.py`, `execution/idempotency.py` | reorganize |
| `setups_report.py`, `funnel_diagnostics.py`, `reaction_funnel.py` | `journal/`, `telemetry/` | reorganize |
| `config_health.py`, `startup_reconciliation.py`, `terminal_cleanup.py`, `stats_ingestion.py`, `multi_match.py`, `worker.py`, `gate.py`, `setup_card.py`, `setup_execution_aggregate.py`, `delivery.py` | mixed — `runtime/`, `journal/`, `telegram/` per content | reorganize, case-by-case |

## `algo-bot/app/bot/*`, `app/signals/*` → target tree

| Current path | Target | Action |
|---|---|---|
| `bot/*` (Telegram client, handlers, keyboards, owner DM) | `telegram/` | reorganize |
| `signals/fx_manual_algo.py`, `manual_execution.py`, `manual_intent.py`, `parsing.py` | `manual_algo/` | reorganize |
| `signals/broadcast.py`, `calendar.py`, `chart_analysis.py`, `reports.py`, `weekly_report.py`, `watcher.py`, `trade_ops.py`, `pips_format.py`, `price.py` | `journal/`, `telegram/` per content | reorganize |

## Configuration, contracts, persistence — unchanged by this task

| Current path | Target | Action | When |
|---|---|---|---|
| `config/apexvoid.yml` + categorized includes | unchanged, already correct per §6 | keep | now shipped to production for Kafka/cTrader/analysis-engine; remaining algo-bot authority cutover is independent |
| `config/trading-bot.yml` | legacy manifest compiler input | keep during transition | cTrader execution still uses the manifest path while V3 transport is live |
| `contracts/configuration/*` | unchanged, already the parity-tested authority | keep | — |
| `contracts/autotrade/*` | `contracts/execution/*` | superseded once `execution.trade-plan.v1`/`execution.trade-event.v1` schemas exist and are adopted | after Kafka cutover |
| `app/persistence/{redis_state,store}.py` | `persistence/` (exists, keep) | keep | — |
| `app/core/*` | split: `market`/`config` concerns → already covered by `analysis-engine`'s Go equivalents; runtime/logging stays `algo-bot/runtime` | reorganize (low priority, small) | Stage 8+ |

## `ctrader-engine/*` — market feed remains Redis

cTrader writes closed bars and spot state to Redis. Analysis Engine consumes
that Redis market-data plane; cTrader has no Kafka market publisher. The
remaining execution event migration below is intentionally separate.

Reviewed this pass; matches its target boundary already (see
`service-boundaries.md`). No files moved. The only future change: once
`execution.trade-plan.v1`/`execution.trade-event.v1` contracts exist and
Kafka cutover happens, `ctrader-engine` consumes/produces those instead of
the current Redis TradePlan V8 payload shape — a transport change, not a
responsibility change.
