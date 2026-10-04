# Analysis Engine

The Go analysis engine is ApexVoid's sole automatic technical authority. It
reads closed market bars from Redis, builds causal market state, evaluates the
configured strategies, maintains opportunity lifecycle and arbitration, and
publishes opportunity events to Kafka.

It does not own account equity, sizing, exposure, Telegram, broker access, or
TradePlan publication.

## Runtime flow

```text
Redis closed bars → market state → technical facts → strategies
  → opportunity lifecycle/arbitration → Kafka analysis events
```

The service entrypoint is `cmd/analysis-engine`. `cmd/replay` drives the same
engine over deterministic captures. `cmd/kafka-admin` is the Kafka topic/admin
utility.

## Package layout

- `internal/marketdata`, `market`, `indicator`: bar history and measurements.
- `internal/structure`, `liquidity`, `techniquezone`, `zone`, `trendline`,
  `context`: technical state and zone construction.
- `internal/strategy`: independent strategy implementations and registry.
- `internal/opportunity`, `arbitration`, `state`, `engine`: lifecycle and
  orchestration.
- `internal/transport`: Redis input and Kafka output.
- `test/`: domain, contract, integration, architecture, and replay tests.
- `testdata/`: reusable behavioral and replay fixtures.

## Development

```bash
go build ./...
go vet ./...
go test ./...
go test -race ./test/engine ./test/liquidity ./test/strategy
```

Keep strategy packages independent of Redis, Kafka, Postgres, Telegram, and
broker code. Add behavior-protecting fixtures under `testdata/` when a replay
or contract case needs durable coverage.
