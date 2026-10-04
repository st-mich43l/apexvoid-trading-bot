# Analysis Engine dependency rules

The Go analysis engine has a one-way dependency graph:

```text
market / telemetry
  → indicator / marketdata / config
  → structure / techniquezone / zone / liquidity / context
  → strategy / confluence / regime / session
  → opportunity / arbitration / state
  → transport
  → engine
```

The architecture test in `analysis-engine/test/architecture` enforces the
allowed package ranks.

Rules:

- Indicators do not import strategies or execution code.
- Technical primitives do not import opportunity publication or broker code.
- Strategy packages do not import Redis, Kafka, Postgres, Telegram, cTrader,
  or Algo Bot.
- Transport is wired by the engine; individual strategies remain pure.
- Replay and visualization consume engine/domain types but are not runtime
  dependencies of technical primitives.
