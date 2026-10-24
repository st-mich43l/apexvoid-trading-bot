# Redis market-data contract

Redis is ApexVoid's authoritative **operational market-data plane**, shared by
`ctrader-engine`, Analysis Engine, Algo Bot and future dashboards. PostgreSQL remains the durable trade/accounting store. Kafka is
reserved for durable business events and commands, never the bar/tick feed;
see [ADR-010](adr/010-redis-market-data-kafka-events.md).

## Series Key

One sorted set per `(symbol, timeframe)`:

```text
bars:{symbol}:{tf}
```

Examples:

```text
bars:XAU:M5
bars:XAU:M15
bars:XAU:M30
```

## ZSET Member

Score is the UTC bar-open epoch seconds. Member is compact JSON:

```json
{"t":4102444800,"o":4100.12,"h":4104.2,"l":4098.5,"c":4101.7,"v":1234}
```

Fields:

| Field | Meaning |
|---|---|
| `t` | UTC bar-open epoch seconds |
| `o` | open price |
| `h` | high price |
| `l` | low price |
| `c` | close price |
| `v` | tick volume |

Only closed bars are written. Forming bars must never enter Redis.

For bars finalized from the live cTrader stream, `o`, `h`, and `l` come from
the live trendbar. Because live trendbars may omit `deltaClose`, `c` is stamped
from the last spot bid observed inside that period and clamped to `[l, h]`. If
the period has no spot, the feed fetches that single historical bar before
writing. Startup always upserts the full configured historical window, allowing
deployments to repair stale or malformed cached bars. Historical repair upserts
do not publish `bars:new` replay events.

## Write Semantics

For a closed bar:

```text
ZREMRANGEBYSCORE bars:XAU:M5 <ts> <ts>
ZADD bars:XAU:M5 <ts> <bar_json>
ZREMRANGEBYRANK bars:XAU:M5 0 -(N+1)
PUBLISH bars:new "XAU:M5:<ts>"
```

Removing by score before `ZADD` guarantees exactly one member per timestamp,
even when backfill overlaps reconnect delivery. `N` is `BARS_WINDOW_MAX`.
Configuration V3 requires up to 2,000 M1 bars, so the current .NET sink's
global manifest cap is set to **2,000** for every timeframe. The sink does not
yet expose per-timeframe caps; it intentionally over-retains H4/D1 rather than
under-retaining the analysis bootstrap window.

## Read Semantics

Consumers read the latest window:

```text
ZREVRANGE bars:XAU:M5 0 <k-1>
```

The returned members are newest-first. Consumers that need chronological
analysis should reverse the window locally.

## Pub/Sub Trigger

Channel:

```text
bars:new
```

Payload:

```text
{symbol}:{tf}:{ts}
```

Example:

```text
XAU:M5:4102444800
```

The publish is a cadence signal only: "new data may exist; re-read Redis".
The ZSET is the material data source. Consumers must not treat pub/sub as
durable. Analysis Engine subscribes before its bootstrap scan, keeps a cursor
per symbol/timeframe, reads every bar newer than that cursor chronologically,
and periodically reconciles the ZSET. A missed notification can delay work;
it cannot create a permanent candle gap.

## Live Spot Key

`ctrader-engine` also writes the latest bid/ask spot as a plain Redis string,
throttled to at least one second between writes per symbol:

```text
SET price:XAU:spot {"bid":4082.10,"ask":4082.30,"ts":4102444800}
```

`ts` is UTC epoch seconds when the spot was observed by the feed. Consumers
must treat this as live only while fresh; Algo Bot falls back to the closed bar
price when it is absent or stale.

## Persistence

Redis is allowed to lose this data on restart. `ctrader-engine` backfills the
configured window from cTrader on startup or reconnect. Deep historical
backtesting storage is a separate future sink, not this Redis contract.

## Auto-Trade Inputs

All fields ending in `*_pips` that cross Redis between the Python gate and the
C# engine are denominated in **0.1 price units for XAUUSD**, independent of the
broker-reported `pipPosition`. Python resolves that unit from its shared
auto-trade units module and C# from `AUTO_TRADE_XAU_PIP_SIZE`; broker metadata is
diagnostic only and must never drive price-to-pip conversion.

The Algo worker reads raw `bars:XAU:M1`, `bars:XAU:M5`, `bars:XAU:M15`, and
`price:XAU:spot` data, plus the Go Analysis Engine's opportunities, and
publishes one TradePlan V8 per admitted setup (see "TradePlan Stream"). The
worker never parses Telegram text, and a candidate is never published to a
separate stream: TradePlan is the only execution contract.

Live Go opportunities are adapted into strategy matches, kept per symbol as one
JSON list:

```text
SET auto_trade:strategy_matches:XAU <json-array>   # TTL follows the latest expiry
```

Each entry carries a stable `match_id`, strategy/mode, direction, entry zone, ATR,
structure swing, targets, reasons, quality, the Go thesis group and the source
timestamp. Invalid, expired or symbol-mismatched entries are removed; a terminal
Kafka event withdraws its match. Every live match competes in the decision cycle
(see [execution](execution.md)).

Same-thesis entry corridors are reserved per symbol in one script:

```text
SET autotrade:entry_overlap:XAU <json-list> EX 2760   # 45-minute window + 60 s
```

The latest operator-facing M1 gate decision is stored at
`auto_trade:last_gate` and `auto_trade:last_gate:{symbol}`. It contains the gate
state, M1 trigger, selected role-aware rail, opposite target, target room, spot
freshness, loaded frame counts, `gate_source`, and active strategy-match
metadata; it is telemetry, not an execution input.

## Auto-Trade State And Events

Live position state lives in the TradePlan runtime
(`execution:plan_runtime:{plan_id}`, see "TradePlan Stream"); there is no
separate per-position key. cTrader Engine also publishes a per-symbol operator
snapshot at `auto_trade:executor_snapshot:{SYMBOL}` (its broker positions and
resting orders, live plan ids, and account equity) that `/algo_status` reads.

The owner kill switch is `auto_trade:paused`; `1` holds new entries (autonomous
and manual) but does not stop existing position management.

Executor lifecycle events are appended as JSON payloads to
`auto_trade:events`. The Python bot persists its delivery cursor at
`auto_trade:telegram_event_cursor`. Open and target events carry their own
initial `stop_pips`; open events also carry the broker-valid `targets_pips`
ladder used by that position, so delivery never reconstructs risk from a
global default.

Scanner-confirmed reaction execution timing is persisted independently from
the setup lifecycle:

```text
SETEX auto_trade:execution_confirmation:{setup_id} <ttl> <json>
```

The JSON records `phase`, deterministic `episode_id`, zone entry/exit
timestamps, the last evaluated closed M1 timestamp, and consumed trigger
evidence. Phases cover immediate M5 authority, waiting for a retest, waiting
for an in-zone M1 trigger, trigger acceptance/miss, publication, expiry, and
invalidation. TTL is at least the setup lifecycle retention. BUY membership
uses executable ask and SELL membership uses executable bid; midpoint is never
execution evidence. This key is Python-owned restart state and is not consumed
by Telegram or used by C# for price-action analysis.

Decision counters are Redis hashes written by `lifecycle.increment_metric`:
`auto_trade:metrics:{SYMBOL}` (field = counter name) and, for a counter with
dimensions, `auto_trade:metrics:{SYMBOL}:{name}` (field = `dimension=value`).
Counters are telemetry only (counted once per admission evaluation of a plan,
so a plan retried on a later scan cycle counts again); a failed increment is
logged and never blocks a plan. Two counters measure the reaction-anchored Go invalidation (the stop of
the seven legacy-family strategies sits beyond the extreme of the reaction's own
bars; Go marks it with the evidence code `invalidation_beyond_reaction_extreme`):

| Counter | Dimension | Incremented when |
|---|---|---|
| `go_invalidation_reaction_anchored` | `strategy` (catalog id) | a Go-origin plan whose envelope carries the evidence code reaches stop planning |
| `go_invalidation_reaction_anchored_too_wide` | `strategy` (catalog id) | `plan_go_invalidation_stop` rejects such a plan with `stop_exceeds_go_invalidation_envelope` (the anchored stop is wider than the instrument envelope maximum) |

The legacy-family strategies (`key_level`, `supply`, `demand`, `order_block`,
`fvg`, `flip_zone`, `trendline`) publish only a structural stop inside the
instrument stop cap (a wider one is refused by Go, never tightened). Go marks
each published candidate with the evidence code `stop_budget_structural`
(stop inside the instrument floor and cap) or `stop_budget_floor` (tighter than
the floor; execution widens it), counted the same way (once per admission
evaluation, failures logged and ignored):

| Counter | Dimension | Incremented when |
|---|---|---|
| `go_stop_budget_structural` | `strategy` (catalog id) | a Go-origin plan whose envelope carries `stop_budget_structural` reaches stop planning |
| `go_stop_budget_floor` | `strategy` (catalog id) | the same, for `stop_budget_floor` |

The dedicated signal bot sends full operational cards only to the configured
owner DM. `SIGNAL_PUBLIC_CHANNEL_ID` remains exclusively for manual broadcasts
from the general bot. Order message IDs are cached for seven days under
`auto_trade:msg:{position_id}` (plus group namespaces) so TP, stop, close, and
scale-in updates reply to their trade root. A missing or rejected Telegram
reply target falls back to a standalone card.

## TradePlan Stream (execution:*)

The only execution contract - see `docs/autotrade-execution-integrity.md`.
Owner-armed manual `/algo` signals use the same stream: the bot converts each
`manual_trade:intents` entry into a TradePlan whose `plan_id` is the intent id
(`manual:{signal_id}:{revision}`) and whose plan-kind label (`strategy_family`) is `manual`.

```text
execution:trade_plans              XADD stream of published TradePlan V8 JSON
execution:plan:{plan_id}           Python plan JSON, short TTL from plan expiry
execution:plan_dedup:{plan_id}     publication tombstone, seven-day default TTL
execution:plan_state:{plan_id}     monotonic mechanical state
execution:plan_claim:{plan_id}     executor claim, 24-hour TTL
execution:plan_runtime:{plan_id}   C# open runtime state
execution:plan_recovery:{plan_id}  C# recovery copy while runtime state is open
execution:plan_ack:{plan_id}       latest structured executor acknowledgement
execution:plan_cancel:{plan_id}    cancel intent written by Algo Bot, 7-day TTL
execution:plan_cancel_ack:{plan_id} executor's report of applying the intent, 7-day TTL
execution:plan_rejection:{id}      durable malformed/unsupported stream record
execution:trade_plan_runtime_ids   tracked C# runtime plan IDs
execution:trade_plan_cursor        last durably handled stream ID
```

### Plan cancel intent

`execution:plan_cancel:{plan_id}` is a Python-written *tombstone*: it may exist
before the plan does. It is written (`SET NX`, first reason wins) when a
Go-derived plan's opportunity is invalidated or expires, or when its authority
scope is rolled back. The executor checks it every poll, before any submission
and independent of the quote, and reports in `execution:plan_cancel_ack:{plan_id}`.
Python never edits `execution:plan_state:*`; the executor stays its single
writer. Field lists, sources and outcomes are pinned in
`contracts/autotrade/plan-cancel-intent.json`, which both test suites read.

| plan stage when the intent is applied | executor action | ack outcome |
| --- | --- | --- |
| received / not yet submitted | never submits; plan cancelled | `cancelled_unsubmitted` |
| resting entry orders, no fill | every pending leg cancelled at the broker (retried until it sticks) | `cancelled_pending_orders` |
| some legs filled, some resting or unsent | unfilled remainder withdrawn; filled legs stay managed | `positions_kept_unfilled_cancelled` |
| every leg already a position | nothing withdrawn, nothing closed | `positions_kept` |

Python atomically checks/sets the dedup tombstone, writes the short-lived plan
payload and `published` state, then appends exactly one stream entry. The
tombstone TTL is `max(24h, auto_algo.lifecycle.candidate.storage_ttl_seconds)`, seven
days by default, so deleting or expiring the payload cannot re-publish the same
deterministic plan ID. Existing pre-tombstone payloads are backfilled on retry.

C# reads after its durable cursor. A valid plan is source-generated JSON
deserialized, validated, claimed, persisted as runtime/recovery state, and
marked `armed` before the cursor advances. A malformed plan is stored under
`execution:plan_rejection:{stream_id}` and publishes `plan_rejected` before
the cursor advances. A transient Redis or broker error leaves the cursor
unchanged for retry. Duplicate claims reconcile the existing state and never
submit another order.

The scalar plan state is the cross-language lifecycle projection:
`published`, `received`, `armed`, `submitted`, `filled`, `managing`,
`completed`, `rejected`, `cancelled`, or `expired`. The acknowledgement key
adds executor timestamp, stream ID, executor label, and optional reason code.
C# recovery payloads use their own key so they do not extend Python's plan
payload TTL. Recovery/runtime keys are removed when execution becomes terminal.

## Analysis Zone Book (2026-09)

```text
analysis:zone_book:{SYMBOL}   Go's live per-symbol zone snapshot, 20m TTL
```

Written by the Go analysis engine (`analysis-engine/internal/transport/redis/zonebook.go`)
after every accepted closed-bar dispatch that produces a snapshot
(`cmd/analysis-engine/main.go`), from that symbol's own already-computed
`map[market.Timeframe]zone.ZoneState` - no separate detector, no extra
Redis read. Reused, not opened separately: the existing `redistransport.Runtime`
connection used for bar ingestion.

```json
{
  "schema_version": 1,
  "symbol": "XAU",
  "generated_at": 1700000100,
  "entries": [
    {"timeframe": "M15", "kind": "supply", "low": 2020.0, "high": 2025.0,
     "atr": 4.0, "strength": 0.8, "touch_count": 2, "state": "fresh"}
  ],
  "barriers": [
    {"side": "sell", "low": 2020.0, "high": 2025.0, "tier": "zone",
     "score": 0.8, "touches": 2, "source_timeframes": ["M15"]}
  ]
}
```

`entries` is the raw snapshot: every tracked timeframe's zones flattened into
one list, unfiltered. `kind` is `supply` or `demand` (other zone kinds are never
written), `state` is the zone's full lifecycle label (`fresh`, `touched`,
`partially_mitigated`, `mitigated`, `invalidated`), and `atr` is the current ATR
of the entry's own symbol/timeframe.

`barriers` is the normalized opposing-structure book, computed by Go
(`analysis-engine/internal/barrier`, a port of the Python StructuralBarrierBook
normalization and parity-tested against it). It is built from `entries` by:

1. keeping only standing zones (`fresh`, `touched`, `partially_mitigated`);
2. keeping only M5/M15/H1;
3. dropping zones outside the execution width gate
   (`execution.policy.execution_zone_max_width_atr` / `_pips`, measured against
   the zone's own timeframe ATR and the instrument's pip size; a zone with no
   positive ATR is dropped);
4. merging touching or overlapping same-side bands (max score, min touches,
   union of `source_timeframes`);
5. reconciling contradictory cross-side bands (the weaker of two substantially
   overlapping opposing bands is dropped; a pass that would drop more than a
   third of the book is discarded).

`side` is `buy` (a demand wall below price) or `sell` (a supply wall above it).
`barriers` is `[]` when Go computed the book and found no opposing structure,
and `null` (or absent) when Go did not compute it; the two must never be
confused.

`schema_version` names the layout (a document without it is version 1; a consumer refuses a version it does
not know) and `generated_at` is the engine's clock at publication, in seconds.

#### What a barrier is, and which zones form it

A barrier is a **price band that opposes an entry**: the union of the live same-side V2 zones (supply for a BUY, demand for a
SELL) that survive steps 1-3 and touch or overlap each other. It is not a zone: a chain of touching zones is one barrier whose
`low`/`high` are the lowest member low and the highest member high, `score` is the strongest member's strength, `touches` the
*least* touched member's, and `tier` is always `zone` today. Nothing about the band, tier, score, merge order or sort order is
changed by the provenance below.

`members` (additive, optional) lists the original zones the band is the union of, in the order the merge visited them
(ascending `low`, then `high`): `id`, `timeframe`, the zone's own `low`/`high`, `state` (`fresh`, `touched`,
`partially_mitigated`), `touches`, `strength`, `origin_time`, `created_at` and `last_touched_at`. A field the engine did not know is
absent (never `null` or `0`). A barrier from an older engine has no `members`. The reader keeps a valid member as published and
drops an unusable one on its own; **a defect in `members` never makes the book unavailable and never changes a barrier or a
decision**, because nothing decides on it.

When the target-room gate rejects a candidate, the route outcome and the existing `v8 build rejected` log line carry
`measured.opposing_barrier` (built from the decision already taken, `describe_blocking_barrier`): the barrier's side, band, tier,
score and touches, `member_count`, `member_ids`, the room in pips, and the **decisive member**, the zone whose own band the rule
looked at (the member that holds the planned entry, or overlaps the candidate band most, for `opposing_entry_contained` /
`_overlap`; the member that owns the nearest edge for `opposing_barrier_no_target`, `_room_below_cost` and `opposing_major_no_room`).
`members_published: false` and no decisive member mean the book had no provenance; nothing is inferred. An admitted plan carries none
of this: TradePlan V8 is unchanged.

Which barrier restricts the reward (`structural_target_room.py`): for a BUY only supply **ahead** of the entry restricts upside, for a SELL
only demand below it. A barrier wholly behind both references the gate knows, the zone's proximal edge (the structural reference) and
the executable price, is set aside before the nearest barrier is chosen (`measured.shared_boundary_state.behind_entry_excluded` /
`behind_entry_bounds`); it used to be picked first because a band overlap ranks at distance zero, report negative raw room and block as
`opposing_barrier_no_target`, and hide any real barrier ahead. A barrier that holds either reference, or lies between the executable price
and the proximal edge, is not behind the entry and still blocks. Without an executable price nothing is set aside. The execution-cost floor
(`opposing_barrier_room_below_cost`) and the containment blocks are unchanged. Separately, `zone.DeriveState` now checks structural validity
for an untouched zone too: price can close beyond a zone without ever trading inside it (a gap, one displacement candle), and such a zone is
`invalidated`, not `fresh`.

Open F-7 decisions (no change here; see `docs/architecture/f7-opposing-barrier-ground-truth.md` section 12): whether a touched zone
should still be a barrier and after how many touches (Go: 30; the frozen Python: the first); whether a union wider than the
per-zone width gate should be one wall; whether a strategy whose thesis is a rotation through a range may be blocked
by that range's own interior. A range scalp is **not** exempt from the shared barrier book in the Go-origin flow (the legacy bypass
needs `full_take_profit_pips`, which no Go opportunity carries); only the M1 scalp (Impulse Pullback) bypasses it.

Consumer: `algo-bot/app/autotrade/go_zone_book.py`. The worker's
execution-time opposing-barrier/target-room recheck
(`structural_target_room.py`) reads `barriers` for a Go-origin `StrategyMatch`
(`GO_ORIGIN_TAG in match.tags`) that does not bypass opposing structure; a Python-origin
match never reads it. Algo Bot adapts the published list and does not re-derive, filter or
normalize structure: there is no Python fallback.

The reader classifies every read into exactly one of three states:

| State | Meaning | Worker |
|---|---|---|
| `AVAILABLE_WITH_BARRIERS` | a valid, fresh book listing one or more barriers | evaluates target room against them |
| `AVAILABLE_EMPTY` | a valid, fresh book that explicitly publishes `barriers: []` | evaluates target room against no barriers (Go found none) |
| `UNAVAILABLE` | the key is missing or expired, Redis failed, the payload is malformed, not an object, for another symbol, from an unsupported `schema_version`, has no `generated_at`, is older than 600 s (`ZONE_BOOK_MAX_AGE_SECONDS`) or more than 60 s in the future, has `barriers` absent or `null` (not computed), or contains any barrier that fails validation (side not `buy`/`sell`, non-finite, non-positive or inverted band, tier not `level`/`zone`/`major`, non-numeric score, negative or non-integer touches) | **holds** the new autonomous plan: route outcome `opposing_barrier` / `waiting` / `zone_book_unavailable` with the reason in `measured`, intent retained, nothing claimed, retried on the next cycle until the match expires; metric `zone_book_unavailable{reason}` |

One invalid barrier makes the whole book unavailable rather than being dropped: dropping it would silently
widen the room in front of an entry. "Go has not told us" never reads as "nothing is in the way". The hold
applies only to building a NEW autonomous initial plan; positions that already exist are managed by the
executor and are not touched.
