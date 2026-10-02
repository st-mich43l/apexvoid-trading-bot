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
`MarketContext`, and immutable snapshots. All 19 Go strategy factories are
enabled in production, and the Kafka opportunity lifecycle is consumed by
Algo Bot's execution-policy pipeline. Bootstrap/replay reconstruction still
suppresses historical publication, but live closed-bar transitions publish
normally. The competing Python detector/scanner graph and duplicate technical
math have been deleted.

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

**Current state**: `app/autotrade/*`, `app/bot/*`, and `app/signals/*` own
execution policy, manual operation, Telegram, journal, and reconciliation.
The automatic worker consumes Go opportunity/technical facts and does not
rebuild technical zones or detectors. CI asserts that retired technical source
paths remain absent and that production modules cannot import them. Retained
`app/scalping/*` modules own outcome accounting, lifecycle, risk and telemetry,
not technical opportunity production.

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

## Closed Python-analysis violations

The former V1-V6 violations are closed by the Go cutover and physical
deletion of the competing Python technical graph. The worker consumes Go
opportunities and Go zone-book facts; retained quote, spread, risk and order
checks do not reconstruct a technical thesis. Configuration V3 deployment
and handler-level configuration governance remain independent tracks.
