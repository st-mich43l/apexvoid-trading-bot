# Analysis Engine (Go)

Analysis Engine answers “what is happening in the market?” It owns technical
facts, strategies, opportunity identity, lifecycle, arbitration, and Kafka
publication. It never owns account policy, broker access, or executable
TradePlans.

## Runtime composition

```text
Redis bars
  → marketdata/history
  → indicator + structure + liquidity + zones + context
  → strategy registry
  → opportunity book + arbitration
  → Kafka lifecycle publisher
```

## Package responsibilities

- `market`, `marketdata`, `indicator`: canonical bar and measurement types.
- `structure`, `liquidity`, `techniquezone`, `zone`, `trendline`, `context`:
  causal technical state.
- `strategy`, `confluence`, `regime`, `session`, `fib`, `mad`: technical
  strategy inputs and independent strategy implementations.
- `opportunity`, `arbitration`, `state`, `engine`: lifecycle and evaluation.
- `transport/redis`, `transport/kafka`: market input and event output.
- `visualization`, `telemetry`: snapshots and operational measurements.

Strategies return technical candidates and must remain independent of Redis,
Kafka, Postgres, Telegram, cTrader, and Algo Bot. The architecture dependency
test enforces this rule.

## Development

```bash
go build ./...
go vet ./...
go test ./...
go test -race ./test/engine ./test/liquidity ./test/strategy
```

Use `cmd/replay` with the committed fixtures in `analysis-engine/testdata/` to
reproduce deterministic engine behavior. New strategy behavior should add a
focused test or replay fixture rather than a second analysis implementation.
