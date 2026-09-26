# S14D: a confirmed Go event becomes a real TradePlan V8, end to end

No new execution path: a Go-origin Kafka event reaches the **unchanged** Auto Algo
pipeline through the reviewed adapter. This phase proves the chain, keeps every
control, and fixes the one identity defect the proof exposed.

```
Kafka record ──▶ durable PostgreSQL ledger ──▶ reviewed Go policy (fence, freshness, adapter)
  ──▶ Redis StrategyMatch + setup ──▶ Auto Algo preflight (worker._handle_event) ──▶ TradePlan V8
  ──▶ execution:trade_plans ──▶ cTrader executor (broker simulator) ──▶ ack + execution events
  ──▶ Telegram delivery + journal
```

## Evidence

| hop | test | environment |
| --- | --- | --- |
| Kafka → plan on the stream | `algo-bot/tests/test_s14d_go_full_chain.py` | real PostgreSQL + real Redis (production Lua), the real worker cycle |
| plan → cTrader acknowledgement, forced fills, injected failures, restart | `ctrader-engine/tests/TradePlanRuntimeTests.GoDerivedPlan.cs` | the executor's own runtime against a broker simulator |
| execution events → Telegram + journal | `algo-bot/tests/test_s14d_executor_events_delivery.py` | real PostgreSQL journal, real delivery handler (Telegram calls stubbed) |

The hops are joined by **the same bytes**: `contracts/autotrade/go-derived-plan-xau-supply.json`
is the plan Python's worker actually published (drift-guarded, normalised for
wall-clock fields; regenerate with `UPDATE_GOLDEN=1`); the C# suite feeds it to the
executor and writes `go-derived-plan-executor-events.json`, the events the Python
delivery suite replays. Nothing places a real order and no market data is invented.

Verified on the published plan: provenance (`plan_id = v8:go_<opportunity>`, setup id =
match id, authority epoch / catalog scope / opportunity tags, `htf_bias_source:go_H1`,
`go_reaction:rejection`), the Go structural zone id, entry band and invalidation, the
confirmation bar time, expiry that never outlives the technical opportunity, the full
Auto Algo contract (`sizing.mode=equity_table`, risk caps, `cancel_on_expiry`), the
executor's own `TradePlan.validate()`, and no `bypass_analysis_gates` anywhere (checked
on the plan and, by AST, in every module of the Go path).

## Controls that still gate a Go-origin match

Each is exercised through the real cycle and leaves the stream empty:
stale spot, outside the reaction publish window, below `min_confluence`, auto-trade
disabled, price outside the entry contract (waits for a retest), a withdrawn opportunity
(`go_plan_withdrawn`), a rolled-back scope (`authority_fenced`). Redelivery and repeated
cycles never duplicate a plan; the executor never duplicates a broker order (retry after
an injected broker failure, redelivery after a restart with duplicate-`ClientOrderId`
rejection enabled) and reconciles a broker-side stop-out without closing anything itself.

## Defect found and fixed: unstable thesis identity

The S13C adapter derived the thesis from the **opportunity id**. Go re-confirms a zone on
later bars under new ids (on the real capture: 39 zones re-confirmed, up to 7 times), so
each re-confirmation was a new thesis and the plan builder's active-thesis claim never
stopped it: the test published **two executable plans for one zone**. The legacy contract
is explicit ("a new confirmation timestamp alone must not create a new thesis"). The
adapter now uses the identical rule and bytes as `structural_reaction_support.thesis_id`
(a parity test pins them) keyed on the Go structural zone id; the same test now shows one
plan and the second setup left unpublished.

## Findings carried to later phases

* **Ladder (S14E).** The plan declares `entry_distribution: zone_scale` with leg ratios
  0.8/0.2 but a `market_watch` entry with no legs; at the fixture's account size the
  executor placed a single market leg. Which ladder Manual Algo actually uses, and the
  worst-case group risk with every leg filled, are S14E.
* **Confluence (S14C).** The plan's `analysis.confluence` still comes from the evidence
  count; no calibrated mapping exists yet.
* **Not covered here:** real cTrader, real Telegram delivery, a live natural setup (the
  S14G canary), and Manual Algo card presentation details (S14E).
