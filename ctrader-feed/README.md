# cTrader Feed

`ctrader-feed` is the cTrader gateway for ApexVoid. It runs as its own .NET
container, writes closed XAU trendbars into Redis, and can execute tightly
guarded demo-account scalp candidates from the private Python auto-scalp gate.

It does not import Python bot code, touch Postgres, or send Telegram messages.
Auto-gate candidates, executor state, and operator events cross only the Redis
boundary documented in
[`../docs/redis-contract.md`](../docs/redis-contract.md).

## Runtime

- .NET 8 console service.
- Official `cTrader.OpenAPI.Net` package (`OpenAPI.Net` RX/protobuf client).
- `StackExchange.Redis` for plain Redis ZSET + Pub/Sub.
- Multi-stage Docker build: SDK build image, `runtime-deps:8.0` final image.
- The default Docker publish mode is Native AOT (`PUBLISH_AOT=true`), using
  `clang` in the build stage and `runtime-deps:8.0` in the final stage. This was
  verified to publish successfully; the official SDK still emits trim/AOT
  analysis warnings from protobuf/RX/WebSocket dependencies.
- Fallback compatibility mode is available with
  `docker build --build-arg PUBLISH_AOT=false`; that produces a trimmed,
  self-contained, single-file binary instead of Native AOT.

## Environment

Required:

```env
CTRADER_CLIENT_ID=
CTRADER_CLIENT_SECRET=
CTRADER_ACCESS_TOKEN=
CTRADER_REFRESH_TOKEN=
CTRADER_ACCOUNT_ID=
```

Optional defaults:

```env
CTRADER_HOST=demo.ctraderapi.com
CTRADER_PORT=5035
CTRADER_SYMBOL=XAUUSD
CTRADER_TIMEFRAMES=M1,M5,M15,M30
CTRADER_BACKFILL_BARS=1500
CTRADER_REQUEST_TIMEOUT=30
CTRADER_TOKEN_REFRESH_MINUTES=50
CTRADER_REFRESH_TOKEN_KEY=ctrader:refresh_token
REDIS_URL=redis://redis:6379/0
BARS_WINDOW_MAX=1500
BARS_CHANNEL=bars:new
BAR_QUALITY_LOOKBACK=6
HEALTH_FILE=/tmp/ctrader-feed.heartbeat
AUTO_TRADE_ENABLED=false
AUTO_TRADE_DRY_RUN=true
AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN=false
AUTO_TRADE_SYMBOLS=XAU
AUTO_TRADE_EXPECTED_BROKER=Fusion
AUTO_TRADE_SL_DISTANCE=6.5
AUTO_TRADE_RISK_PCT=2.0
AUTO_TRADE_PIP_VALUE_PER_LOT=10.0
AUTO_TRADE_TP_PIPS=30,60,90,120,200
AUTO_TRADE_TP_WEIGHTS=20,20,20,20,20
AUTO_TRADE_BE_BUFFER_PIPS=3
AUTO_TRADE_CANDIDATE_MAX_AGE=90
AUTO_TRADE_SPOT_MAX_AGE=5
AUTO_TRADE_MAX_SPREAD_PIPS=5
AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS=10
AUTO_TRADE_MAX_DAILY_TRADES=6
AUTO_TRADE_STREAM=auto_trade:candidates
AUTO_TRADE_EVENT_STREAM=auto_trade:events
AUTO_TRADE_LABEL=apexvoid-auto
AUTO_TRADE_MAX_TRANCHES=2
AUTO_TRADE_ADD_RISK_FRACTION=0.5
AUTO_TRADE_ADD_MAX_AGE_BARS=3
AUTO_TRADE_ADD_COOLDOWN_BARS=3
AUTO_TRADE_ADD_LEVEL_BUFFER_ATR=1.0
AUTO_TRADE_ADD_STOP_BUFFER_ATR=0.3
AUTO_TRADE_ADD_MIN_STOP_PIPS=15
AUTO_TRADE_ADD_REQUIRE_RISK_FREE=false
AUTO_TRADE_ZONE_FILL_ENABLED=false
AUTO_TRADE_ZONE_FILL_MIN_ATR=0.5
AUTO_TRADE_ZONE_FILL_TTL_BARS=3
```

`CTRADER_SYMBOL=XAUUSD` is published under Redis symbol `XAU`, producing keys
such as `bars:XAU:M5`.

## Auth Notes

Create a cTrader Open API application in the cTrader portal. Market data needs
account access; auto-trading additionally requires a token granted with the
`Trading` scope and `FullAccess` on the selected account. Store the client
ID/secret plus access/refresh tokens in the deployment environment. Tokens are
never logged. The service first tries the supplied access token and refreshes
only after rejection or on the configured rotation interval.

When cTrader rotates the refresh token, the latest value is persisted in Redis
at `CTRADER_REFRESH_TOKEN_KEY` (default `ctrader:refresh_token`) with a SHA-256
fingerprint of the `.env` seed token. The rotation is reused only while that
fingerprint still matches. Supplying a newly authorized token in `.env`
automatically starts a fresh rotation chain; legacy bare-token cache values are
also replaced automatically. Redis is internal to the compose network and not
exposed publicly; token values are never logged.

Demo is the default endpoint:

```env
CTRADER_HOST=demo.ctraderapi.com
CTRADER_PORT=5035
```

Auto-trading has an independent hard lock that refuses live accounts even if
the host is changed. It also requires a Hedged account and a broker name
matching `AUTO_TRADE_EXPECTED_BROKER`.
Set `AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN=true` to disable execution whenever the
token grants any live account, even when the selected account is demo.

## Demo Auto-Trader

The independent auto-scalp worker publishes only fresh, news-cleared
`Auto Range Scalp` candidates. It reads raw cTrader M1/M5/M15 OHLC directly and
does not consume scanner detections, forming signals, Market Map entries, or
Telegram state. M5/M15 build role-aware support/resistance rails; M1 must reject
a rail, while a directional M5 impulse blocks fading into active momentum. The
nearest opposite-role rail must leave at least 30 pips of room.
The executor revalidates candidate age, live quote age, spread, entry distance,
account identity, and the configured XAU tranche limit before placing an order.
Telegram is an operator surface, never the execution trigger.

Initial size is `min(risk-based, equity-table)` using realised account balance,
the structure stop, and `AUTO_TRADE_RISK_PCT`. The exposure table uses the
operator bands:
`$200-$500 -> 0.02-0.05`, `$500-$1,000 -> 0.05-0.11`,
`$1,000-$2,000 -> 0.11-0.21`, `$2,000-$3,000 -> 0.21-0.31`, and
`$3,000-$5,000 -> 0.31-0.36` lots. The result is floored to `0.01` lots;
balances below `$200` are rejected and balances above `$5,000` stay capped at
`0.36` lots. Stops use the latest directional swing plus `0.3 ATR`, clamped to
15-65 pips.

After TP1 has banked profit and moved the initial stop through breakeven, a
fresh same-direction displacement and BOS may open one independent momentum
tranche. Sizing is bounded by remaining table exposure, the group loss ceiling,
and the add-risk cap. Every tranche keeps its own structure stop and target
ladder. Adds at an adverse price or while the group is losing are refused as
averaging down. `AUTO_TRADE_ADD_REQUIRE_RISK_FREE=true` applies the stricter
post-add non-negative worst-case rule.

When explicitly enabled, a zone at least `0.5 ATR` wide is planned as two limit
legs at the proximal edge and midpoint. They share the original stop and split
the planned ladder proportionally; an unfilled midpoint leg is cancelled after
three M1 bars. This remains disabled by default.

Weighted partial closes use `30/60/90/120/200` pips. A `0.02`-lot position
exits at TP1 and TP3, `0.03`
uses TP1-TP3, `0.04` uses TP1-TP4, and positions from `0.05` use all five.
TP1 moves the stop to `BE+3`, TP2 keeps that stop unchanged, TP3 moves it to
TP1, and TP4 moves it to TP2.
Existing positions retain the targets, TP ordinals, and slices encoded in their
original cTrader comment.

`/auto_pause` blocks new entries and `/auto_resume` releases the block.
`/auto_status` reports mode, open auto positions, the UTC daily trade count,
and the latest private M1 gate state and selected rail.
Existing positions continue to receive TP management while entry is paused.

Before enabling, run the read-only diagnostic:

```bash
/app/ctrader-feed --account-check
```

Set `AUTO_TRADE_ENABLED=true` with `AUTO_TRADE_DRY_RUN=true` first when rolling
out a new account. Actual demo orders require `AUTO_TRADE_DRY_RUN=false`.

## Data Flow

1. Resolve `CTRADER_SYMBOL` to `symbolId` and `digits`.
2. Upsert the full `CTRADER_BACKFILL_BARS` historical window on startup;
   reconnects only backfill the missing gap.
3. Subscribe to live spot + live trendbar streams.
4. Hold each live trendbar while it is forming.
5. When the next period begins, stamp the previous close from the last
   in-period spot bid and clamp it to the trendbar range. If no in-period spot
   is available, fetch that single historical bar as an authoritative fallback.
6. For each live closed bar: remove any existing member at that score, `ZADD`,
   trim newest `BARS_WINDOW_MAX`, then `PUBLISH`.

The feed logs one raw historical and one raw live trendbar per timeframe after
each connection. These diagnostics include `hasDeltaClose` but no credentials.
It also warns when `BAR_QUALITY_LOOKBACK` consecutive live bars close at the
same range extreme.

## Healthcheck

The app writes a heartbeat file after backfill, after subscribe success, every
successful closed-bar write, and every received cTrader heartbeat. Compose calls
the same binary with `--healthcheck`; it exits non-zero if the heartbeat is
missing or older than ten minutes. This tracks connection liveness even during
weekends or quiet market periods when no XAU bars close.

## Local Commands

```bash
dotnet test tests/CTraderFeed.Tests.csproj
docker build -t apexvoid-ctrader-feed:local ctrader-feed
# fallback mode if an SDK update regresses AOT compatibility:
docker build --build-arg PUBLISH_AOT=false -t apexvoid-ctrader-feed:trimmed ctrader-feed
# operator escape hatch after supplying the normal feed environment:
dotnet run --project src/CTraderFeed.csproj -- --reset-token-cache
```

Run via root compose:

```bash
docker compose up -d --build redis ctrader-feed
docker compose logs -f ctrader-feed
```
