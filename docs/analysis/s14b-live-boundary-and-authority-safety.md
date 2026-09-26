# S14B: live activation boundary, freshness, withdrawal and rollback

Scope: everything between "Go published a confirmed XAU `supply`/`demand`
opportunity" and "a TradePlan V8 may exist for it", plus what happens to that
plan when the reason to trade it disappears. Nothing here grants authority: no
scope moves to Go until an operator records an acceptance and runs `grant`
(see `s13-python-retirement.md`). This document describes the guards that must
hold *once* one has.

## 1. Only new, confirmed, in-window opportunities may become plans

`GoOpportunityPolicy.on_creation` evaluates a pure gate
(`app/analysis_client/freshness.py`) after the fence says Go owns the scope.
Every decision row records the technical, publication and consumption times.

| reason code | meaning |
| --- | --- |
| `pre_activation_event` | observation (`created_at`) predates the scope's durable go-effective boundary |
| `opportunity_expired` | the engine's own `expires_at` has passed at consumption |
| `event_too_old` | observation older than `analysis.technical_authority.max_event_age_seconds` (default 900) |
| `delivery_lag_exceeded` | Kafka publish → consume lag above `max_delivery_lag_seconds` (default 300) |

The **go-effective boundary** is durable and already audited: it is the end of
the drain window of the `python → draining → go` handover
(`AuthorityRecord.go_effective_at`, returned on the Go `AuthorityDecision`). A
rollback followed by a fresh grant moves it forward, so events observed while
Python owned the scope are history, never new plans. Events failing the gate are
still applied to the durable ledger: history and invalidations stay correct; they
just never become matches. A renamed/wiped consumer group re-reading the topic
from the beginning therefore rebuilds the ledger and trades nothing.

Idempotency is kept: a redelivery of an opportunity that was already adapted
(e.g. the process died between the match write and the decision row) completes
the same write and is *not* re-judged as stale.

## 2. Disabling the consumer is not a rollback

`consumer_enabled` is a config flag, not fence state. Previously, with it off,
`authorize_legacy_match` allowed every legacy Python plan without looking at the
fence, so a scope still recorded as Go-owned could get a Python plan. Now:

* an `AuthoritySnapshot` (all non-Python scopes) is loaded at boot, before any
  background task runs, and refreshed by a supervised `authority_watch_loop`;
* consumer off + scope Go-owned or mid-handover → legacy plans **denied**
  (`scope_owned_by_<owner>_consumer_disabled`);
* the snapshot **fails closed**: stale beyond 90 s, or never loaded once the
  watch loop is running → catalog-mapped scopes denied;
* no per-plan DB read is added (the snapshot is process-local).

Every boot appends a row to `analysis_authority_runtime_audit`:
`runtime_boot`, `consumer_enabled_changed` (differs from the previous boot), or
`consumer_disabled_go_scopes_fail_closed` (off while a scope is not Python-owned;
that scope then publishes from *nobody* until a fenced rollback completes).

## 3. What happens to Go-derived work when it must stop

Triggers: Kafka invalidation, Kafka expiry (`SETUP_EXPIRED`), or an authority
rollback. Implemented in `app/autotrade/go_plan_cancel.py` (Python) and
`TradePlanRuntime.ApplyPlanCancelIntentsAsync` (executor); the Redis contract is
pinned by `contracts/autotrade/plan-cancel-intent.json`.

| state of the Go-derived work | handling |
| --- | --- |
| match in `strategy_matches:{symbol}`, setup pre-plan | match removed, setup `INVALIDATED`/`EXPIRED` |
| setup `plan_built` | match removed, setup `CANCELLED` |
| plan queued on `execution:trade_plans`, not submitted | cancel intent; executor never submits it |
| plan submitted, entry orders resting, nothing filled | intent; every pending leg cancelled at the broker (retried until it sticks); plan cancelled |
| partially filled ladder | filled legs are positions: they keep their stop and normal TP/BE management; only unfilled/unsent legs are withdrawn |
| open position | **never closed** by an authority change or invalidation |

The intent is a tombstone written even if no plan exists yet, and the worker
refuses to publish a Go match that has one (`go_plan_withdrawn`), so a plan
racing the withdrawal cannot slip through. Python-owned scopes never get one.
Ownership governs plan *creation*, never management of positions that exist.

Known, accepted limitation: a plan cancelled before any fill leaves its setup at
`plan_published` and its thesis claim held until the plan's own expiry (Python
does not yet consume the executor's ack to release them). The plan itself is
withdrawn; nothing can trade.

## 4. Rollback: tested safe sequence

```
python -m app.scripts.analysis_authority rollback --symbol XAU --scope supply \
    --expected-epoch <n> --actor <name> --reason <text>
```

1. **Fence flips first.** Go can no longer create matches or publish plans
   (`not_go_owner:*`, `authority_fenced`). Python resumes only after
   `drain_until`.
2. **Then withdrawal** (section 3) for that scope, including the executor
   intents. `rollback-all` does both for every Go-bound scope.
3. If step 2 fails after step 1 (Redis down) the command prints the error and
   exits **3**; `python -m app.scripts.analysis_authority withdraw --symbol XAU
   --scope supply --actor <name> --reason <text>` re-runs it. It is idempotent
   and the first recorded reason is kept.

Verify with `execution:plan_cancel_ack:{plan_id}` (outcomes in the contract file).

## 5. Test evidence

Run against real PostgreSQL and a real Redis (production Lua, `REAL_REDIS_URL`):
`tests/test_s14b_go_lifecycle.py` (boundary, all four gate reasons, history
replay, redelivery after a crash, invalidation/expiry, racing publish, registered
plans, the full rollback sequence and its re-run, commit-discipline across a
crash/restart, poison record), `tests/test_s14b_authority_boundary.py`,
`tests/test_s14b_freshness.py`, `tests/test_s14b_plan_cancel_contract.py`,
CLI tests in `tests/test_analysis_authority_cli.py`, and C#
`TradePlanRuntimeTests.CancelIntent.cs` (every plan stage, broker-failure retry,
restart, no-quote, dry-run, unparseable intent, shared-contract parity).

## 6. Not covered here (later phases)

Real policy dry-run shadow (S14A), Go-vs-Python replay comparison (S14C), the
full-chain broker-simulator test (S14D), ladder spec and worst-case group risk
(S14E), production shadow/acceptance (S14F) and the operator-approved cutover
(S14G). No production evidence is claimed by this change.
