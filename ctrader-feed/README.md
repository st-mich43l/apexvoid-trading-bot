# cTrader Feed

`ctrader-feed` is the market-data producer for ApexVoid. It runs as its own
.NET container, connects outbound to cTrader Open API, and writes closed XAU
trendbars into Redis for downstream scanners and dashboards.

It does not import Python bot code, touch Postgres, send Telegram messages, or
place trades. The only shared boundary is the Redis contract in
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
CTRADER_TIMEFRAMES=M5,M15,M30
CTRADER_BACKFILL_BARS=1500
CTRADER_REQUEST_TIMEOUT=30
CTRADER_TOKEN_REFRESH_MINUTES=50
CTRADER_REFRESH_TOKEN_KEY=ctrader:refresh_token
REDIS_URL=redis://redis:6379/0
BARS_WINDOW_MAX=1500
BARS_CHANNEL=bars:new
BAR_QUALITY_LOOKBACK=6
HEALTH_FILE=/tmp/ctrader-feed.heartbeat
```

`CTRADER_SYMBOL=XAUUSD` is published under Redis symbol `XAU`, producing keys
such as `bars:XAU:M5`.

## Auth Notes

Create a cTrader Open API application in the cTrader portal, grant read-only
account access, and store the client ID/secret plus access/refresh tokens in
the deployment environment. Tokens are never logged. The service application
auths, refreshes the access token, account-auths, resolves the symbol, then
backfills and subscribes.

When cTrader rotates the refresh token, the latest value is persisted in Redis
at `CTRADER_REFRESH_TOKEN_KEY` (default `ctrader:refresh_token`) and preferred
over the deployment environment on the next reconnect. Redis is internal to the
compose network and not exposed publicly; do not log this key or its value.

Demo is the default endpoint:

```env
CTRADER_HOST=demo.ctraderapi.com
CTRADER_PORT=5035
```

Switching to live later should be a deliberate environment change.

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
