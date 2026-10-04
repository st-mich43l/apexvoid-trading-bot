# Scalp execution lane

Analysis Engine publishes the technical opportunity and confirmation. Algo Bot
applies the scalp execution policy: quote freshness, entry location, spread,
session, exposure, sizing, and TradePlan construction.

The scalp lane keeps its own execution geometry. It does not rebuild zones,
swings, liquidity, or indicators from Python market data. A scalp opportunity
is eligible only when the Go event contains the required technical facts and
the current execution context passes policy.

Scalp TradePlans use the configured instrument policy for stop distance,
targets, break-even, and volume. The resulting plan is written to the normal
Redis TradePlan stream and is executed by cTrader Engine; there is no second
technical publisher or broker path.

Operational checks:

- inspect `analysis.opportunity.v1` and arbitration events for the technical
  lifecycle;
- inspect Algo Bot policy outcomes for quote, spread, session, exposure, and
  risk decisions;
- inspect `execution:trade_plans` and execution events for the broker path;
- use the configured replay fixtures for deterministic policy regression.
