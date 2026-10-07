# cTrader Engine

`ctrader-engine` is ApexVoid's .NET broker gateway. It subscribes to cTrader
market data, writes closed bars to Redis, consumes validated Redis TradePlans,
and manages orders and position lifecycle.

It does not import Algo Bot code or access PostgreSQL. Technical opportunity
generation belongs to the Go Analysis Engine; this service only validates and
executes the completed TradePlan contract.

## Runtime

- .NET 8 console service.
- cTrader Open API client and StackExchange.Redis.
- Native AOT publish by default; set `PUBLISH_AOT=false` for the trimmed
  self-contained fallback image.
- Configuration is supplied through the resolved runtime manifest and secret
  bootstrap environment.

## Development

```bash
dotnet build src/CTraderFeed.csproj --nologo
dotnet test tests/CTraderFeed.Tests.csproj --nologo
```

See [`../docs/redis-contract.md`](../docs/redis-contract.md),
[`../docs/execution.md`](../docs/execution.md),
and [`../docs/configuration.md`](../docs/configuration.md).
