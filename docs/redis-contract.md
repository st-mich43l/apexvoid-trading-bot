# Redis Bar Contract

Redis is the reconstructable market-data cache shared by `ctrader-feed`,
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

`ctrader-feed` also writes the latest bid/ask spot as a plain Redis string,
throttled to at least one second between writes per symbol:

```text
SET price:XAU:spot {"bid":4082.10,"ask":4082.30,"ts":4102444800}
```

`ts` is UTC epoch seconds when the spot was observed by the feed. Consumers
must treat this as live only while fresh; the scanner falls back to the closed
bar price when it is absent or stale.

## Persistence

Redis is allowed to lose this data on restart. `ctrader-feed` backfills the
window from cTrader on startup or reconnect. Deep historical backtesting storage
is a separate future sink, not this Redis contract.

## Auto-Trade Candidate Stream

When enabled, the scanner appends qualified `Range Edge Scalp` candidates to:

```text
XADD auto_trade:candidates MAXLEN ~ 1000 * payload <json>
```

The versioned payload contains a deterministic `candidate_id`, symbol,
timeframe, direction, trigger timestamps, trusted live price, scored key level,
entry-zone bounds, confluence count, and reasons. Publishing fails closed when
the spot is absent/implausible or a high-impact event is active or unavailable.
Candidate claims and outcomes use `auto_trade:executor:candidate:{id}` for
restart-safe idempotency; the stream cursor is `auto_trade:cursor`.

## Auto-Trade State And Events

Open executor state is stored at `auto_trade:position:{position_id}`, with the
tracked IDs in the `auto_trade:positions` set. This holds initial/remaining
native volume, five broker-valid slices, target progress, direction, and fill.
It allows cTrader reconciliation to resume partial TPs after restart and remove
state for positions closed by broker SL or manually.

UTC daily entry counts use `auto_trade:daily:{yyyyMMdd}:trades`. The owner kill
switch is `auto_trade:paused`; `1` blocks new entries but does not stop existing
position management.

Executor lifecycle events are appended as JSON payloads to
`auto_trade:events`. The Python bot persists its delivery cursor at
`auto_trade:telegram_event_cursor` and sends only operational events to the
owner through the dedicated signal bot.
