# Redis Bar Contract

Redis is the reconstructable market-data cache shared by `ctrader-engine`,
the Python price-action scanner, and future dashboards. Postgres remains the
durable trade/accounting store. Market bars do not belong in Postgres.

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
even when backfill overlaps reconnect delivery. `N` is `BARS_WINDOW_MAX`,
default `1500`.

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

The publish is a cadence signal only: "a closed bar arrived, pull the window".
The ZSET is the material data source.

## Live Spot Key

`ctrader-engine` also writes the latest bid/ask spot as a plain Redis string,
throttled to at least one second between writes per symbol:

```text
SET price:XAU:spot {"bid":4082.10,"ask":4082.30,"ts":4102444800}
```

`ts` is UTC epoch seconds when the spot was observed by the feed. Consumers
must treat this as live only while fresh; the scanner falls back to the closed
bar price when it is absent or stale.

## Persistence

Redis is allowed to lose this data on restart. `ctrader-engine` backfills the
window from cTrader on startup or reconnect. Deep historical backtesting storage
is a separate future sink, not this Redis contract.

## Auto-Trade Candidate Stream

All fields ending in `*_pips` that cross Redis between the Python gate and the
C# engine are denominated in **0.1 price units for XAUUSD**, independent of the
broker-reported `pipPosition`. Python resolves that unit from its shared
auto-trade units module and C# from `AUTO_TRADE_PIP_SIZE`; broker metadata is
diagnostic only and must never drive price-to-pip conversion.

When enabled, the Algo worker appends private strategy candidates and completed
scanner strategy matches to:

```text
XADD auto_trade:candidates MAXLEN ~ 1000 * payload <json>
```

The private strategies read raw `bars:XAU:M1`, `bars:XAU:M5`,
`bars:XAU:M15`, and `price:XAU:spot` data. The scanner bridge reads a
short-lived typed match from `auto_trade:strategy_match:{symbol}`. It never
parses Telegram text. Scanner detectors already decide which strategy matches;
the worker does not reclassify it by regime or demand another M1/M5 signal.

Generic scanner matches become `auto_strategy_match` v4 candidates with their
detector setup name, M5 source, structure stop context, and target ladder.
`Range Edge Scalp` remains one strategy and uses the existing
`auto_box_scalp` v3 candidate with stable range bounds and one 50- or 70-pip
full-position target. Publishing fails closed when the spot is absent/stale,
structure-stop context is unavailable, or a high-impact event is guarded.
Candidate claims and outcomes use `auto_trade:executor:candidate:{id}` for
restart-safe idempotency; the stream cursor is `auto_trade:cursor`. Raw
Telegram cards and legacy untyped scanner payloads are never accepted for
execution.

The strategy-match contract has its own version and TTL:

```text
SETEX auto_trade:strategy_match:XAU 420 <json>
```

It contains a stable `match_id`, detector strategy/mode, direction, entry zone,
ATR, structure swing, targets, reasons, and source timestamp. Range-specific
bounds exist only for `Range Edge Scalp`. Invalid, stale, symbol-mismatched, or
malformed matches are removed or ignored. A fresh scanner match has priority
over private strategies for that execution tick.

Used box edges are disarmed until a closed M1 price crosses the box midpoint.
Confirmed broken box IDs are retired for the configured TTL. The latest
operator-facing M1 gate decision is stored at
`auto_trade:last_gate` and `auto_trade:last_gate:{symbol}`. It contains the gate
state, M1 trigger, selected role-aware rail, opposite target, target room, spot
freshness, loaded frame counts, `gate_source`, and active strategy-match
metadata; it is telemetry, not an execution input.

## Auto-Trade State And Events

Open executor state is stored at `auto_trade:position:{position_id}`, with the
tracked IDs in the `auto_trade:positions` set. This holds initial/remaining
native volume, the position-specific broker-valid weighted slices and targets,
their original TP ordinals, target progress, direction, fill, and latest managed
stop. It allows cTrader reconciliation to resume partial TPs and monotonic
trailing after restart while
preserving legacy plans encoded in existing position comments, and removes
state for positions closed by broker SL or manually.

UTC daily entry counts use `auto_trade:daily:{yyyyMMdd}:trades`. The owner kill
switch is `auto_trade:paused`; `1` blocks new entries but does not stop existing
position management.

Executor lifecycle events are appended as JSON payloads to
`auto_trade:events`. The Python bot persists its delivery cursor at
`auto_trade:telegram_event_cursor`. Open and target events carry their own
initial `stop_pips`; open events also carry the broker-valid `targets_pips`
ladder used by that position, so delivery never reconstructs risk from a
global default.

The dedicated signal bot sends full operational cards only to the configured
owner DM. `SIGNAL_PUBLIC_CHANNEL_ID` remains exclusively for manual broadcasts
from the general bot. Order message IDs are cached for seven days under
`auto_trade:msg:{position_id}` (plus group namespaces) so TP, stop, close, and
scale-in updates reply to their trade root. A missing or rejected Telegram
reply target falls back to a standalone card.
