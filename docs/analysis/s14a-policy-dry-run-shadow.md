# S14A: real policy dry-run shadow

`go_shadow` used to record a static `contract_gap`: it could not say what the
live pipeline would do with a Go opportunity. Since the S13B `technical_context`
block it can, and now does, without any side effect.

## What runs

For each durable creation event in `go_shadow` mode, after the ledger write,
`GoShadowPolicy.dry_run_creation` (`app/autotrade/go_shadow_policy.py`):

1. checks scope (`REVIEWED_SCOPES`), that the opportunity is still `active`, that
   `multiple_matches_enabled` holds (otherwise the live worker would not read the
   match), and the freshness gate (expiry, event age, delivery lag; **no**
   activation boundary exists in shadow);
2. translates the event with the *same* adapter as the live cutover
   (`build_strategy_match`, epoch 0); a missing fact is a `rejected` decision,
   never a guess;
3. seeds an in-memory overlay with the setup and match and runs the **real**
   `worker._handle_event` for that match (admission, arbitration, killzone / news,
   execution policy, sizing, the TradePlan V8 build and publish);
4. reads the route outcome and the plan back from the overlay and records one
   decision row in `analysis_shadow_decisions` (`mode = go_shadow`).

| outcome | meaning |
| --- | --- |
| `would_publish` | the live pipeline would have published this plan (`details.plan`, `details.plan_digest`) |
| `would_wait` | valid, but the pipeline waits (`waiting_retest_entry_zone`, `stale_spot`, session window ...) |
| `would_reject` | blocked by a policy/guard; `reason` is the gate's own code |
| `would_not_route` | the cycle recorded no route for the match |
| `not_adapted` | stopped before policy (`scope_not_reviewed`, `event_too_old`, `delivery_lag_exceeded`, `opportunity_expired`, `multiple_matches_disabled`, `symbol_not_auto_trade_enabled`, `ignored_not_active`) |
| `rejected` | the adapter refused (`reaction_confirmation_unavailable`, `technical_context_unavailable`, ...) |
| `dry_run_error` | the dry run itself failed; recorded, the consumer continues |
| `shadow_unavailable` | no dry-run policy is wired into the evaluator |

`details` carries the freshness timings (observed / published / consumed), the
gate's route (`stage`, `status`, `reason_code`, `measured`), the overlay's audit
(`written_keys`, `writes`, `real_reads`) and `fence_actual`: the authority fence's
*real* state for the scope, recorded because the dry run does not apply it.
Decisions are idempotent per `(opportunity, event, mode, outcome)`.

## Why it has no side effects

Layered, each layer enforced independently (`app/analysis_client/shadow_overlay.py`):

* **Redis** — `OverlayRedis`: reads of keys the dry run has not written go to
  production Redis through a guard that raises on any command outside the
  read-only allowlist; writes (after copying the key's current value in, so
  read-modify-write behaves as live) stay in process memory; deletes are
  tombstones; unsupported commands (Lua, pub/sub, consumer groups, anything
  unknown) raise `ShadowSideEffectError`. Streams are overlay-only.
* **The shared client** — `redis_state.client_override` (a `ContextVar`) makes
  any helper that reaches for the global client get the overlay, for the dry
  run's asyncio context only; concurrent live tasks are unaffected.
* **PostgreSQL** — `store.readonly_db` (same mechanism): only plain
  `SELECT` / `WITH` statements; anything else raises `DryRunWriteError`.
* **Telegram** — `_publish_trade_plan_v8` returns before the root-card ensure
  when the client is an overlay.
* **The authority fence** protects *live* publication. It is bypassed only for
  an `OverlayRedis` (identity check on the client class, whose writes cannot
  reach anything); a real client always takes the fenced branch (tested).

## Evidence (`tests/test_s14a_shadow_dry_run.py`, `test_s14a_shadow_overlay.py`)

Real PostgreSQL and a real Redis (`REAL_REDIS_URL`), the actual worker cycle:

* full keyspace dump of production Redis (type, value, TTL presence) is
  **identical** before and after; only read commands reached it; no stream entry;
  PostgreSQL gained exactly the one decision row;
* the plan the dry run records has the **same digest and the same entry / stop /
  targets / risk / sizing / management / execution policy** as the plan the live
  (granted) path publishes from identical inputs;
* `would_wait` (`waiting_retest_entry_zone`), `would_reject`
  (`confluence_below_minimum`), every pre-policy stop, adapter rejection and a
  failing dry run all leave production untouched;
* the Telegram root-card path is never invoked; Postgres writes raise; the fence
  bypass exists only for the overlay.

## Limits (stated, not hidden)

* One evaluation at consumption time. A resting zone that is not yet reached
  records `would_wait`; the eventual outcome is the subject of S14C replay and the
  S14F acceptance, not of this decision.
* Lua is unavailable in the overlay, so setup transitions and plan publication run
  their existing non-atomic fallback inside the dry run. The plan/route logic is
  identical; the atomicity of the production Lua is exercised by the S14B and
  existing real-Redis suites.
* Nothing here proves production behaviour: shadow evidence from a live
  deployment (S14F) is an operator step and is not claimed by this change.
