# Service Boundaries

Frozen ownership per service. "Must not own" lists are as binding as "owns"
lists — a PR that adds one of them to the wrong service is a boundary
violation, not a style choice.

## `analysis-engine` (Go)

**Owns:**

```text
market data normalization · OHLC history · tick/quote history (where useful)
indicators · volatility
pivots · swings · market structure · BOS · CHoCH · protected highs/lows · structure hierarchy
liquidity · equal highs/lows · liquidity pools · liquidity sweeps
supply/demand · order blocks · FVG/iFVG · breaker blocks · flip zones · mitigation state
dealing range · market regime · session context · HTF context
technical strategy detection · opportunity lifecycle · entry geometry · invalidation · targets · evidence
analysis snapshots · visualization · telemetry
```

**Must not own:**

```text
Telegram
account risk budget · account equity policy · daily trading limits · portfolio exposure policy
manual operator commands · journal formatting · trade records
broker authentication · broker order placement · position management
```

**Current state**: Structure V2, Liquidity V1, and the canonical Zone V1
domain are implemented and contract-tested. `internal/zone` owns per-origin
Supply/Demand, Order Block, FVG/iFVG, Breaker, and Flip geometry plus
lifecycle/relevance state; it is wired through `SymbolState`,
`MarketContext`, and immutable snapshots. Strategy evaluation and the final
Python technical-authority cutover remain later migration stages; legacy
detectors are therefore still retained until those stages are complete.

## `algo-bot` (Python)

**Owns:**

```text
auto algo · manual algo
trade eligibility · account/exposure policy · risk allocation · duplicate opportunity protection
TradePlan construction
Telegram · operator workflow
journal · trade records
execution-event handling · reconciliation orchestration
notifications · reporting
```

**Must not own:**

```text
calculating technical market structure itself
```

**Current state**: `app/autotrade/*`, `app/bot/*`, `app/signals/*` already
match this boundary in spirit. `app/analysis/*` and `app/scalping/*` violate
it directly — see the violations table below. Legacy `app/analysis/` is
**not** deleted by this task (per the task's own §38/§57: only after
`analysis-engine` is authoritative and cutover is proven).

## `ctrader-engine` (.NET)

**Owns:**

```text
cTrader authentication · market-data feed · bar/tick publication
broker account state
order placement · order modification · position lifecycle
SL · TP · BE · partial close · broker reconciliation
execution events
```

**Must not own / must not decide:**

```text
FVG validity · market bias · BOS/CHoCH · liquidity sweep quality · strategy confidence · technical setup validity
```

**Current state**: matches the target boundary today. Reviewed this pass —
`ctrader-engine/src/*.cs` (all 47 files) contains order/position/stop
mechanics (`StopTrailPlanner`, `TradePlanExecutionEngine`,
`AutoTradeEngine`, `ProtectiveStop`-equivalents, `VolumePlanner`,
`ExposurePolicy`) and configuration/feed plumbing
(`ResolvedRuntimeManifest*`, `CTraderOpenApiFeedClient`,
`InstrumentRuntimeRegistry`). No file computes FVG/BOS/swing/zone
geometry from raw candles — the engine consumes a TradePlan's already-decided
entry/stop/target geometry and executes it. **No violations found.**

## Current boundary violations (full detail)

| ID | What | Where | Fix scope |
|---|---|---|---|
| V1 | Two divergent ATR formulas live simultaneously (simple mean vs. Wilder RMA, 6.7% apart on real data), feeding different geometry in the same detection pass | `app/analysis/math_utils.py::atr_series` vs `app/analysis/indicators.py::atr` | Owner decision required (which is canonical) before Go Stage 2 continues; see `go-analysis-migration-audit.md` §2.1 |
| V2 | `detectors.build_context()` recomputes ATR with the divergent formula instead of reusing `analyze()`'s | `app/analysis/detectors.py` | Same as V1 |
| V3 | A second, parallel structure/zone stack recomputes from raw OHLC with a hardcoded `atr_length=14`, discarding zones already built for the same window in the same pass | `app/analysis/structure.py`'s non-canonical wrapper functions, called from `detectors.py`'s fallback paths | Behavior-affecting fix, not pure translation — flag to owner at Stage 2/6 cutover, per audit §2.3 |
| V4 | A third, independent swing algorithm (bare fractal, different tie-breaking rule than `find_swings`) for the scalp lane | `app/scalping/context.py::_swings_from_ohlc` | Owner decision: unify or keep as a documented, intentionally distinct variant |
| V5 | `worker.py` independently recomputes ATR→displacement→supply_demand→mitigation three times for HTF opposing-zone vetoes; self-documented, architecturally justified (autotrade's veto path has no live `AnalysisContext`), but still duplicate CPU and a drift risk if `atr_length` changes between call sites | `app/autotrade/worker.py::_htf_zones` + 2 siblings | Collapse once `SymbolState` is reachable from the autotrade path (Stage 5+) |
| V6 | `app/scalping/` is simultaneously an independent analysis stack (own context, own swings) **and** an independent orchestration stack (own risk, own activation, own strategies) — the single clearest case of "algo-bot calculating technical market structure itself" | `app/scalping/*` (~1.7MB, ~20 modules) | Largest open architecture decision — see risk #5 in the architecture report |
| V7 | Possible business-threshold reads directly from `runtime_config` inside Telegram/journal handlers, bypassing a risk/policy boundary | `app/bot/handlers/*`, `app/signals/*` | Not exhaustively audited this pass — migration backlog, unconfirmed severity |
| V8 | (none) | `ctrader-engine` | — |
| V9 | Production still runs pre-V3 configuration (`config/trading-bot.yml`); V3 (`config/apexvoid.yml`) is proven and wired live in local/dev only | deployment | Independent migration track (Configuration V3 Stage C7+), not blocked on this task |

Violations are documented, not fixed, per this task's own scope (§60: "Do
not necessarily fix them in this task. They become migration backlog
items.").
