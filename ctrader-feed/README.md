# cTrader Feed

`ctrader-feed` is the cTrader gateway for ApexVoid. It runs as its own .NET
container, writes closed XAU trendbars into Redis, and can execute tightly
guarded demo-account scalp candidates from the Python scanner.

It does not import Python bot code, touch Postgres, or send Telegram messages.
Scanner candidates, executor state, and operator events cross only the Redis
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
AUTO_TRADE_EXPECTED_BROKER=Fusion
AUTO_TRADE_SL_DISTANCE=6.5
AUTO_TRADE_TP_PIPS=30,50,70,90,130
AUTO_TRADE_CANDIDATE_MAX_AGE=90
AUTO_TRADE_SPOT_MAX_AGE=5
AUTO_TRADE_MAX_SPREAD_PIPS=5
AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS=10
AUTO_TRADE_MAX_DAILY_TRADES=6
AUTO_TRADE_STREAM=auto_trade:candidates
AUTO_TRADE_EVENT_STREAM=auto_trade:events
AUTO_TRADE_LABEL=apexvoid-auto
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
at `CTRADER_REFRESH_TOKEN_KEY` (default `ctrader:refresh_token`) and preferred
over the deployment environment on the next reconnect. Redis is internal to the
compose network and not exposed publicly; do not log this key or its value.

Demo is the default endpoint:

```env
CTRADER_HOST=demo.ctraderapi.com
CTRADER_PORT=5035
```

Auto-trading has an independent hard lock that refuses live accounts even if
the host is changed. It also requires a Hedged account and a broker name
matching `AUTO_TRADE_EXPECTED_BROKER`.

## Demo Auto-Trader

The scanner publishes only fresh, news-cleared `M1 Decision Scalp` candidates.
M5/M15 dealing-range edges and validated barriers are clustered into decision
zones; their biases grade context but do not veto every counter-bias M1 trade.
M1 must confirm the focus zone with a breakout then retest/hold or a sweep then
reclaim. A raw momentum breakout candle never triggers an automatic order, and
a sweep opposing both M5 and M15 requires a multi-timeframe zone.
The executor revalidates candidate age, live quote age, spread, entry distance,
account identity, and the one-XAU-position limit before placing a market order.
Telegram is an operator surface, never the execution trigger.

Balance tiers are fixed: below `$500` does not trade; `$500` uses `0.08` lot,
`$1,000` uses `0.12`, `$2,000` uses `0.20`, and `$5,000+` uses `0.30`. Broker symbol metadata
converts lots to native volume and validates minimum/step/maximum volume. The
default stop is `$6.5`; five client-managed partial closes trigger at
`30/50/70/90/130` pips. A server-side stop is attached to the initial order and
confirmed at the absolute fill price immediately afterward.

`/auto_pause` blocks new entries and `/auto_resume` releases the block.
`/auto_status` reports mode, open auto positions, the UTC daily trade count,
and the latest M1 gate state and focus zone.
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
```

Run via root compose:

```bash
docker compose up -d --build redis ctrader-feed
docker compose logs -f ctrader-feed
```
