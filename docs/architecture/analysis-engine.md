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
- `legacyread`: the detector-contract read (below).
- `opportunity`, `arbitration`, `state`, `engine`: lifecycle and evaluation.
- `transport/redis`, `transport/kafka`: market input and event output.
- `visualization`, `telemetry`: snapshots and operational measurements.

Strategies return technical candidates and must remain independent of Redis,
Kafka, Postgres, Telegram, cTrader, and Algo Bot. The architecture dependency
test enforces this rule.

## Detector-contract read

Five strategies (`break_retest`, `range_edge`, `snap_back`, `momentum_ride`,
`fade_scalp`) reproduce frozen detector decisions, which were defined over
bounded windows with their own swing algorithm. `internal/legacyread` computes
that read once per closed bar from the canonical candles the engine already
owns, using the exact-parity ports in `techniquezone` and the shared `fib`,
`regime`, `momentum`, `session` and `trendline` primitives: M5/M15/H1 windows
from `analysis.legacy_read.window_bars`, swings and structure, key levels,
displacement zones merged, mitigation-stamped and scored against higher
timeframes, liquidity pools and grabs, sessions, trendlines, the scalp barriers
and range, regime, momentum and the higher-timeframe bias. It is attached to
`context.MarketContext` (`Legacy`) and each `TimeframeContext` (`Legacy`).

It is not a second source of bars or a second structure authority: the
hierarchical structure remains the read for every other strategy, and the
detector-contract read is derived only from the same closed candles. Detector
volatility is the Wilder-smoothed true range the frozen detectors used, distinct
from the simple ATR series of the analysis steps. All thresholds are in
`config/analysis.yml` (`analysis.legacy_read`, including the frozen compat-helper
defaults under `analysis.legacy_read.compat`, and `analysis.strategies.range_edge`).

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
