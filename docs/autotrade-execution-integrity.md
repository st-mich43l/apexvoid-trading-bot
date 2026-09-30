# Auto-trade execution integrity

This contract keeps ranked intent selection, risk validation, Redis ownership,
and executor recovery consistent across the Python publisher and C# executor.
When any ownership or stop-plan evidence is uncertain, execution fails closed.

## Ranked cycle ownership

One closed M1 event is one arbitration cycle. The worker ranks all executable
intents first, then acquires:

```text
auto_trade:cycle_route_lock:{SYMBOL}:{cycle_id}
```

The cycle lock serializes evaluation of the complete ranked list. A separate
per-route lock:

```text
auto_trade:route_lock:{SYMBOL}:{match_id}
```

prevents two workers from evaluating the same StrategyMatch concurrently. The
route lock does not choose the cycle winner. Both lock types use random owner
tokens, a finite TTL, and compare-and-delete Lua release so an expired owner
cannot delete a successor's lock.

Publication returns a typed result. `duplicate_candidate`,
`route_in_progress`, `cycle_conflict`, `duplicate_reaction`,
`duplicate_thesis`, and `publication_unavailable` all preserve the preferred
route and block lower-ranked intents. Only an intent-specific
`terminal_reject` permits fallback. The authoritative cycle winner is stored
at:

```text
auto_trade:cycle_owner:{SYMBOL}:{cycle_id}
```

Duplicate event delivery therefore cannot replace the first valid winner.

## Final protective-stop pipeline

Python and C# share one final protective-stop planner (`stop_plan_version=2`).
The planner includes every deterministic transformation before broker
submission:

1. base structure stop;
2. ATR structure buffer;
3. sweep/wick invalidation;
4. min/max stop envelope;
5. symbol rounding (midpoint away from zero);
6. opposing-zone evaluation;
7. optional push beyond an execution-grade opposing zone;
8. final envelope validation after the push;
9. final stop distance and stop pips;
10. final reward/risk calculation.

### Base stop versus final broker stop

- **Base stop** is the structure/wick result after envelope clamp and rounding.
- **Final stop** equals the base stop unless an execution-grade opposing zone
  contains the base stop and push is enabled.
- Context-only opposing zones (wider than configured pip/ATR limits) remain
  telemetry and never mutate the stop.
- Push disabled with stop inside the zone rejects with
  `stop_inside_opposing_zone`.
- Push enabled moves BUY to `zone_low - buffer_atr×ATR` and SELL to
  `zone_high + buffer_atr×ATR`, then re-validates side-of-entry and max
  envelope. A pushed stop that exceeds the envelope is rejected; it is never
  clamped back into the zone.

### Candidate contract fields

Candidate payload v5 with `stop_plan_version=2` carries:

```text
planned_stop_entry_price
planned_base_stop_price / planned_base_stop_pips
planned_final_stop_price / planned_final_stop_distance / planned_final_stop_pips
planned_stop_price / planned_stop_distance / planned_stop_pips   # final aliases
planned_stop_raw_price
planned_stop_clamped
stop_source
stop_adjustment                    # none | opposing_zone_push
stop_adjustment_zone_id/low/high
stop_plan_version                  # 2
```

Legacy `planned_stop_*` values are the **final** stop so older readers still
see the broker stop. Candidate contract advertisement remains v6 during
rolling deploy; stop-plan version is the authoritative planner schema.

### Route-first validation

A stop is only meaningful next to the entry it protects, so the executor
resolves the route **before** it plans or validates any stop:

1. resolve the route and its planned entry geometry (market quote, single
   limit price, zone-fill reference entry and leg prices);
2. validate the entry contract against Python's declared route and planned
   entry;
3. recompute the final stop at that planned entry;
4. validate the final stop contract and final RR;
5. size, submit, and never touch entry or stop again.

Python publishes the route it resolved and the entry it priced:

```text
planned_execution_route     # market | single_limit | zone_split
planned_entry_price
planned_leg_entry_prices    # committed legs only (single limit / zone split)
entry_plan_version          # 1
```

For strict autonomous candidates (`entry_plan_version >= 1` and
`stop_plan_version >= 2`), Python resolves a concrete route before stop
planning. Unresolved `either` is not permitted with an exact final-stop
contract; the executor rejects legacy strict+`either` payloads with
`final_stop_entry_route_invalid`. Legacy candidates without those plan
versions may still declare `either`.

A committed planned entry must match the executor's planned entry within
`AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS` (never less than one tick), and a
market route must additionally still be within tolerance of the executable
quote; otherwise the candidate is rejected with
`final_stop_entry_drift_rejected`.

### Executor entry-distance hard cap

Adaptive strategy drift (observation tolerance) and the executor hard cap are
separate. Only `AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS` may authorise Algo bot READY
and candidate publication. Distance uses the executable broker quote
(BUY=`ask`, SELL=`bid`) versus the entry zone. Python emits Algo bot WAIT when
outside the cap and retains the setup; C# re-checks before broker mutation.

### Break-even after confirmed TP1

After a broker-confirmed partial close at TP1, a single-position route moves
the stop to **BE+N broker ticks** anchored to the actual fill. This is **not**
trading pips.

Canonical config: `AUTO_TRADE_BE_BUFFER_TICKS` (default `6`).

For XAU with `digits=2`, tick size is `0.01`. The buffer is on the profit side
of the broker fill:

- BUY: `entry + ticks × tick_size` → BE+6 = `entry + 0.06`
- SELL: `entry - ticks × tick_size` → BE+6 = `entry - 0.06`

Examples: BUY `4087.66` → `4087.72`; SELL `4087.66` → `4087.60`.

Manual multi-leg ladders are the deliberate exception. TP1 does not bunch
each runner at its own fill-level BE. The executor solves one shared absolute
stop from booked TP pip-volume, remaining-entry VWAP, remaining volume, and
original group volume. If every remaining clip stops there, the complete
group still retains the configured positive tick buffer. The stop is rounded
toward protection and can never loosen an owner/current SL. After TP2, every
surviving manual leg advances to the owner's absolute TP1 price.

Never multiply the BE buffer by `AUTO_TRADE_XAU_PIP_SIZE` (0.1). The deprecated
alias `AUTO_TRADE_BE_BUFFER_PIPS` is interpreted as a tick count during
migration only.

Target evaluation requires a quote newer than the fill
(`spot.Timestamp > OpenedAt`). One trade group produces one canonical terminal
Telegram result.

### Executor validation and entry tolerance

The executor recomputes the complete final plan at the planned entry.
Prices/distances may differ by at most one symbol tick; boolean, version,
source and adjustment must match exactly. Mismatch rejects with
`final_protective_stop_contract_mismatch`, or with
`final_stop_zone_identity_mismatch` when only the opposing-zone identity
disagrees.

No function may mutate the stop after contract validation. The validated
final stop is the broker stop.

If the market moves enough that the recomputed final contract diverges beyond
tolerance, reject. Do not silently widen the stop or preserve stale Python RR.

### The approved absolute stop survives fill slippage

The broker receives the exact approved absolute stop, never
`fill ± distance`. Before submission the stop must still be on the losing
side of the executable entry and RR must still clear the policy minimum. A
market fill that slips does **not** move the stop: the amendment carries
`stopPlan.StopLoss` and the observed slippage is published as
`execution_slippage` telemetry only (`final_stop_absolute_applied` counts the
applied stop). An amendment whose acknowledgement is not authoritative is
broker-outcome-unknown, not a licence to re-derive the stop
(`final_stop_amendment_unknown`).

### Opposing-zone identity

A pushed stop must name the exact zone it was pushed beyond. Python publishes
`opposing_zone_id` and `stop_adjustment_zone_id/low/high`; when a zone has no
stored id both sides derive the same deterministic fingerprint:

```text
symbol | timeframe | side | low | high | created_bar_ts | source
```

For `stop_adjustment = opposing_zone_push` the executor requires a non-empty
identity on **both** sides, an exact id match, and low/high within one tick.
A missing identity is a mismatch, never an implicit pass. For
`stop_adjustment = none` the executor must also produce no adjustment, so a
pushed contract can never validate against an unpushed plan or vice versa.
Context-only zones produce no push on either side and therefore need no push
identity.

### Final reward/risk

Python preflight RR uses `final reward distance / final stop distance`.
C# independently rechecks RR after final-stop reconstruction for every
autonomous initial strategy with a policy minimum RR, using the same
`fill_relative` / `absolute` / `hybrid` target model. Pullback adds keep their
dedicated stop model and are not validated against the initial candidate stop
contract.

## Structured candidate execution state

Redis key `auto_trade:candidate:{id}` stores a structured record:

```json
{
  "candidate_id": "candidate-123",
  "stream_event_id": "1720000000000-0",
  "state": "published",
  "lease_token": null,
  "lease_expires_at": null,
  "attempt": 0,
  "last_error": null,
  "outcome": null,
  "updated_at": 1720000000,
  "version": 1
}
```

Every state belongs to exactly one category, and the category - never string
matching - decides what may happen next:

| Category | States | Meaning |
| --- | --- | --- |
| Active lease-owned | `processing`, `broker_submitting` | One executor owns it until its lease expires |
| Retryable | `published`, `retryable_error` | No broker side effect; claimable now |
| Recovery-required | `broker_outcome_unknown`, `broker_reconciling` | A broker request may exist; only the recovery claim policy may move it |
| Terminal | `ordered`, `completed`, `rejected`, `dry_run`, `flip_pending`, `integrity_error` | Immutable outcome |

`broker_reconciling` is the recovery-**active** state: it marks a candidate
currently owned by a recovery worker. It is structurally separate from
`processing` so a crashed recovery worker can never decay into a reclaimable
normal execution — an expired `broker_reconciling` lease stays
recovery-required and may only be resumed by another recovery claim.

Allowed transitions:

```text
published            → processing
retryable_error      → processing
processing           → broker_submitting | rejected | retryable_error
broker_submitting    → ordered | broker_outcome_unknown | rejected
broker_outcome_unknown → broker_reconciling (recovery claim; never processing)
broker_reconciling   → ordered    (adoption)
                     → rejected   (confirmed non-acceptance)
                     → retryable_error (only after durable absence quorum)
                     → broker_outcome_unknown (timeout before quorum)
ordered | completed | rejected | dry_run | flip_pending | integrity_error
                     → immutable
```

`broker_outcome_unknown` can never be released to `retryable_error` directly:
the release is reserved for the recovery-active state after the absence
quorum has been durably proven. An expired `broker_submitting` lease is also
reclaimed only by the recovery policy, and the reclaim writes
`broker_reconciling` (with `last_error` remembering the recovered-from state),
never `processing`.

Readers also parse legacy plain strings (`published`, `processing`,
`ordered:<id>`, `rejected:<reason>`, `dry_run`, `flip_pending:<…>`). An
unrecognised legacy string is a conflict, never an implicit retry.

### Claim disposition and the stream cursor

`TryClaimCandidateAsync` returns a typed disposition instead of a status
string, and the cursor follows the disposition:

| Disposition | Cursor | Executor action |
| --- | --- | --- |
| `Claimed` | hold until done | process the candidate |
| `ActiveElsewhere` | hold | another owner holds an unexpired lease |
| `Terminal` | advance | outcome already exists (`candidate_terminal_cursor_advanced`) |
| `RecoveryRequired` | hold | reconcile the broker (`candidate_recovery_required`) |
| `Conflict` | hold | integrity error; surface it (`candidate_state_conflict`) |

A claim policy states explicitly what a caller may reclaim, so an ordinary
intake claim can never adopt a `broker_submitting`, `broker_outcome_unknown`
or `broker_reconciling` record; only the recovery flow can. An *expired*
`broker_submitting` or `broker_reconciling` lease also returns
`RecoveryRequired` under the default claim policy — never a normal reclaim
that could place a duplicate order.

## Publication authority and all-or-nothing recovery

Production candidate publication uses Redis Lua for isolated claim checks and
one `XADD`. Redis script execution is isolated from other clients, but it does
not roll back commands already executed when a later command fails. The
candidate stream event is therefore the authoritative publication record.

Every event embeds the candidate, reaction, thesis, and cycle-ownership
identities needed for recovery. Reconciliation is two-phase:

1. **Validate** every target key (candidate state, stream-event marker,
   reaction claim, thesis claim, cycle owner). Conflict if any key belongs to
   a different identity. Compatible executor-progress states for the same
   candidate and stream event (`processing`, `ordered`, `rejected`,
   `retryable_error`, `broker_outcome_unknown`, …) are not conflicts.
2. **Restore** missing keys only when every key is compatible. Never reset a
   progressed executor state back to `published`. Never call `XADD`. Never
   partially restore if a later key conflicts.

Reconciliation decodes structured records with `cjson.decode` rather than
pattern matching, so a candidate id, outcome, or error string containing JSON
punctuation cannot forge a field.

## Token-owned fenced executor leases

`TryClaimCandidateAsync` returns a `CandidateExecutionLease` with a
cryptographically strong token, stream event id, and expiry. Acquire is
allowed from `published`, from `retryable_error`, or from an expired lease for
the same candidate and stream event, and each reclaim mints a fresh token and
increments `attempt` (`candidate_retry_reclaimed`).

Every lease-owned mutation - renew, transition, complete, release,
broker-unknown, rollback ownership - requires **all** of:

```text
candidate_id matches
AND stream_event_id matches
AND the record carries a lease token
AND that token equals the caller's token
AND the transition is allowed from the current state
```

A record with no current token authorises nothing: after a successor completes
and clears the token, a stale predecessor still cannot write
(`executor_stale_complete_blocked`, `executor_stale_release_blocked`).
Terminal records are immutable to any token. Lease expiry is evaluated from
Redis `TIME` inside Lua, not host clocks, and every script encodes with
`cjson.encode`.

`CompleteCandidateAsync`, `ReleaseCandidateAsync`,
`TransitionCandidateStateAsync` and `RenewCandidateLeaseAsync` all return
whether the fenced write happened, and every call site inspects the result. A
completion that fails after the broker accepted an order does not report
success: it publishes an integrity alert and reconciles by deterministic
client order id.

### Lease heartbeat

A claim starts a `CandidateLeaseHeartbeat` that renews the lease every 30s
inside a 2-minute window, so a long market placement, a multi-leg zone fill,
a stop amendment, a rollback or a reconciliation cannot outlive its own lease
(`executor_lease_heartbeat_success` / `executor_lease_heartbeat_failed`). The
heartbeat exposes ownership loss, cancels broker work while cancelling is
still safe, and stops at terminal completion. A stale heartbeat cannot renew a
successor's lease.

Ownership is re-verified immediately before **every** broker side effect,
including each zone-fill leg. Loss before the request aborts without touching
the broker (`executor_lease_lost_before_broker`). Loss after a possibly
accepted request is not a retry: it becomes `broker_outcome_unknown`
(`executor_lease_lost_after_broker`).

## Broker uncertainty and adoption

Errors are classified by what the broker may have done, not by exception type:

| Situation | State | Cursor |
| --- | --- | --- |
| Failure before the request left the executor | `retryable_error` | hold, retry later |
| Confirmed rejection | `rejected` | advance |
| Response lost, timed out, or unverifiable | `broker_outcome_unknown` | hold until resolved |
| Configuration fatal | session stops | untouched state |

A `BrokerOutcomeUnknownException` carries the candidate and the deterministic
client order id, and the outer handler preserves the recovery state instead of
releasing it to `retryable_error` (`broker_outcome_unknown_preserved`). Metric
semantics are separated:

- `broker_response_unknown` — acknowledgement is uncertain while the lease may
  still be held;
- `executor_lease_lost_after_broker` — the lease was lost after a broker
  request may have begun;
- `final_stop_amendment_unknown` — the order exists but stop amendment
  acknowledgement is uncertain.

The group plan is **kept** while the outcome is unknown, because it holds the
identities needed to adopt the order (`candidate_id`, `stream_event_id`,
route, `client_order_ids`, `submitted_at`, recovery counters).

### Absence must not be confirmed from one snapshot

Recovery uses the strongest available evidence, in order:

1. query / match by deterministic `client_order_id`;
2. search pending orders and positions by that identity;
3. search by exact candidate identity in broker metadata;
4. only then an absence-confirmation quorum.

A single empty broker snapshot never becomes confirmed absence. When direct
client-order lookup is unavailable, recovery requires consecutive empty
authoritative snapshots separated by
`AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS` (default 3s), with
`AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS` (default 2) empties required, within
`AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS` (default 30s). The candidate is
`broker_reconciling` while the recovery owner works and returns to
`broker_outcome_unknown` on timeout; normal intake returns `RecoveryRequired`
throughout and cannot retry.

### Durable, fenced, time-separated confirmations

Confirmation progress is persisted in Redis
(`auto_trade:recovery_progress:{candidate_id}`) and incremented by a fenced
Lua script that:

- uses Redis `TIME` — never the executor host clock — for the eligibility
  decision;
- refuses to count a snapshot until
  `current_redis_time - last_check_at >= AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS`
  (`broker_recovery_confirmation_deferred`), so an immediate process restart
  cannot accelerate the quorum;
- only counts for the exact candidate identity, stream event, current recovery
  lease token and `broker_reconciling` state — a stale recovery owner is
  fenced out instead of counting;
- survives recovery crashes: a successor resumes from the persisted count and
  timestamp.

Startup validation fails closed (`AutoTradeConfigurationException`) when
`AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS < 2`, when the recheck interval or
recovery timeout is not positive, or when the timeout cannot cover
`recheck × (confirmations − 1)`.

The whole recovery operation — group-plan load, every broker snapshot, the
delays between snapshots, and the final fenced mutation — runs under its own
`CandidateLeaseHeartbeat`. While it renews, a second recovery worker cannot
claim the candidate; on ownership loss the linked cancellation token stops
broker queries and delays, and the stale worker leaves the record, the group
plan and the persisted absence progress untouched for the successor
(`broker_recovery_ownership_lost`).

Typed dispositions:

```text
AdoptedPosition | AdoptedPendingOrder | ConfirmedAbsent | StillUnknown | Conflict
```

Broker evidence matching priority: exact broker `ClientOrderId` equality
(`broker_recovery_direct_lookup` — counted only for a real exact match), then
exact persisted client-order identity in broker metadata, then the legacy
candidate-token fallback for objects that carry no client-order identity at
all (`broker_recovery_legacy_token_lookup`). An object that references the
candidate but carries a *different* exact client-order identity is a
`Conflict`: it is never adopted and blocks absence confirmation. A partial
zone outcome (one visible leg, or a filled leg plus a pending leg) is adopted
as-is — never treated as absence, never re-submitted.

Metrics: `broker_recovery_started`, `broker_recovery_direct_lookup`,
`broker_recovery_legacy_token_lookup`, `broker_recovery_empty_snapshot`,
`broker_recovery_confirmation_deferred`,
`broker_recovery_confirmation_recorded`, `broker_recovery_absence_confirmed`,
`broker_recovery_ownership_lost`, `broker_recovery_still_unknown`,
`broker_recovery_conflict`, `broker_outcome_adopted`,
`broker_duplicate_prevented`.

The group plan is deleted only after adoption with persisted execution state,
confirmed absence (durable quorum, released under the recovery fence), or an
explicitly verified rollback. Every broker-submitting route persists the
**resolved** route and the exact client order IDs it is about to submit
(`av-{candidate}` market, `av-{candidate}-l1` single limit,
`av-{candidate}-z1`/`-z2` zone split) into the group plan *before* the first
broker side effect — never the declared candidate route, which may be
`either`.

## Route and planned-entry contracts

Supported declared routes: `market`, `single_limit`, `zone_split`, `either`.
Unknown values fail closed (`final_stop_entry_route_invalid`) before any broker
mutation. Committed routes require `planned_entry_price`; limit and zone routes
also require matching `planned_leg_entry_prices`. Explicit `either` leaves the
executor free to choose a supported route but never reinterprets an invalid
route string as either.

## Structured candidate identity

Legacy plain markers (`published`, `processing`, `ordered:<id>`, …) remain
conservatively compatible; legacy status is determined **only** by the
original raw value being one of those supported plain-string markers.

A structured (JSON) record must explicitly carry the full schema, validated
identically by the Python parser, the C# parser, the Redis execution Lua and
the publication-reconciliation Lua:

```text
version           integer, exactly the supported version (1); never defaulted
candidate_id      string; empty/missing is a conflict, never an implicit pass
stream_event_id   string; empty/missing is a conflict, never an implicit pass
state             non-empty known state; never defaulted to published
```

A structured record never becomes legacy because fields are missing, a
missing or malformed `version` is never interpreted as version 1, and a
missing `state` never falls back to `published`. Malformed structured records
fail closed everywhere: Python raises, C# raises, the execution Lua returns a
claim conflict and refuses every mutation, and publication reconciliation
refuses restoration.

## Lifecycle events versus lifecycle transitions

Event history is append-only. Only explicit state-changing events update
lifecycle snapshots. Unknown or telemetry types (`warning`, `config_health`,
`account_capability`, `ready`, `range_flip_attempted`, `stop_moved`, …)
append history with `mutates_lifecycle=false` and **do not** default to
`managing`.

Candidate lifecycle keys are written only for a real candidate or group
owner. Service readiness uses readiness/config keys and must never create
`lifecycle_state:service`; session-level failures publish `service_error`.

An operational failure that a retry can fix must not create a terminal
lifecycle state, or the successful retry's own transitions would be refused as
backward:

| Event | Lifecycle state | Terminal |
| --- | --- | --- |
| `candidate_retryable_error` | `retryable_error` (`lifecycle_retryable_error`) | no |
| `broker_outcome_unknown` | `broker_outcome_unknown` (`lifecycle_broker_recovery`) | no |
| `candidate_lease_lost` | none - history only | n/a |
| `candidate_integrity_error` | `integrity_error` | yes |
| `candidate_terminal_error` | `error` | yes |
| `service_error` | none - no candidate owner | n/a |
| generic `error` | none - history only | n/a |

From either nonterminal recovery state the lifecycle may move forward again
(`executor_received`, `routing_selected`, `order_submitted`, `order_accepted`,
`order_filled`, `rejected`) for the same candidate identity. Terminal states
are never reopened by later telemetry.

Current-state records are structured JSON (`owner_id`, `state`,
`previous_state`, `event_type`, `event_id`, identities, `terminal`,
`version`). Impossible backward transitions are rejected as telemetry/
invalid metrics; documented recovery transitions
(`executor_received → order_filled`, missing → managing with evidence)
remain allowed. Range-side state updates only for mapped lifecycle
transitions.

```text
executor_received → routing_selected → order_planned → order_submitted
  → order_accepted → order_filled → managing → partially_closed → closed
rejected | expired | cancelled | invalidated | error   (terminal)
```

## Group-plan retention

`auto_trade:group_plan:{group_id}` is written only immediately before a broker
request that needs reconnect/adoption metadata. It expires after
`AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS` (with a five-minute floor), never
persistently.

The plan is retained for accepted pending orders, newly filled limits, and
reconnect-adopted positions. It is deleted after a submission failure with a
**confirmed** absence of any broker order, verified partial zone-placement
rollback, owner cancellation, expiry with no remaining order or position,
range-flip cancellation/closure, full TP, owner close, or any other terminal
group state with no pending order or tracked position. Paused,
exposure-rejected, and dry-run candidates never create a plan.

An uncertain broker response never deletes the plan: it is the only record of
the client order ids adoption needs.

## Route recovery

Route history remains append-only evidence. The current route snapshot clears
an old `terminal_reason_code` when a later transition reaches `checking`,
non-terminal `waiting`, `candidate_published`, `executor_received`,
`order_submitted`, or `order_filled`. A new terminal transition replaces the
current terminal reason without deleting the prior history.

## Rollout compatibility

Recommended sequence:

1. Deploy Redis-compatible readers that parse legacy and structured candidate
   states, including the new terminal / retryable / recovery categories.
2. Deploy the executor with reclaimable retry, exact-token fencing, heartbeat
   and broker-unknown adoption.
3. Deploy updated Python publication/reconciliation (cjson decode of every
   known state).
4. Switch publishers to `stop_plan_version=2` final-stop fields and the
   entry-plan contract (`planned_execution_route`, `planned_entry_price`, …).
5. Verify config health; keep execution demo-only during observation.
6. Remove legacy compatibility only in a later PR.

Legacy interpretation stays conservative:

- terminal outcomes (`ordered:…`, `rejected:…`, `dry_run`, `flip_pending:…`)
  remain terminal;
- a bare `processing` remains active until its Redis TTL expires;
- an unrecognised legacy string is a conflict, never a retry;
- structured records are required for any new fenced mutation;
- old executors must not treat a structured JSON blob as a claimable plain
  `published` string.

If a rolling deploy cannot guarantee that, pause intake first:

```text
AUTO_TRADE_ENABLED=false
→ wait for active execution to settle
→ clear only confirmed nonterminal stale records
→ deploy executor
→ deploy publisher
→ verify config health
→ re-enable demo intake
```

Do not enable live accounts.

## XAU risk-leg execution policy

`execution.reaction_risk_leg.enabled` is the single switch for the executor-
injected XAU RISK leg (fixed 0.02/0.05 lots depending on equity, 15 pips
inside the stop), matching the Manual Algo mechanism. It applies to every
XAU multi-leg TradePlan regardless of whether its technical provenance is Go
or Python. Technical source ownership must not alter execution leg geometry.
An explicit `risk_leg:disabled` tag remains available as a plan-level opt-out.

The gate exists because worst-case group risk is a function of the full
group — declared ladder plus any injected risk leg — not of either leg in
isolation. `xau_ladder.worst_case_group_loss` computes that combined
worst case (ladder loss at every leg's widest permitted stop, plus the risk
leg's own fixed worst case) so it can be reviewed against account equity
before the leg is used in production; `risk.max_group_risk_percent`
is declarative only — the executor never reads it, so an absurdly tight cap
does not by itself stop an order. Turning the gate on requires that review
to have actually happened: an accepted ceiling for group worst case, and
something enforcing it.

## Production-path Redis tests

Tests marked `real_redis` / `REAL_REDIS_URL` require a real disposable Redis
server (Redis 7 via `docker run --rm -p 6379:6379 redis:7-alpine`). They
exercise the actual Lua path with no FakeRedis fallback and verify concurrent
candidate/cycle ownership, reaction/thesis consistency, fail-closed
behaviour, readiness on invalid script output, orphan reconciliation without
a second stream event, executor-progress-compatible recovery (including
`retryable_error` and `broker_outcome_unknown`), and fenced lease
acquire/renew/complete/release. FakeRedis tests cover compatibility only and
are not evidence of production Lua atomicity.
