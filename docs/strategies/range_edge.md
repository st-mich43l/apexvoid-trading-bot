# Range Edge Strategy V2

`range_edge` trades the edge of the engine's local scalp range, built on the
detector-contract frame by a faithful port of the frozen scalp structure:

- **Barriers.** Wick-rejection contacts and micro-swing contacts are clustered
  (tolerance from ATR, candle noise, pip size and a configured minimum), merged
  into touch episodes, and kept only with the configured touches and at least two
  wick rejections. Body holds raise the score and grade but are not wick
  evidence. A barrier with `break_closes` consecutive accepted closes is broken.
- **Fallback barrier.** When exactly one side exists, a controlled fallback may
  create the other from the local extreme after the first edge's first touch,
  within the configured width range, and only with the configured number of
  independent confirmations (touches, a wick rejection, a session level, inside
  closes). It is never created from a round number alone.
- **Range states.** `confirmed`, `post_impulse` (a large displacement followed by
  a contracted rotation), `provisional` (one fallback edge, when enabled) or
  `broken` (accepted breakout displacement), ranked confirmed, post-impulse,
  provisional, then quality, distance to equilibrium, width and levels.
  Minimum/maximum width, minimum room and `minimum_inside_closes` gate a range.

The detector evaluates the nearer edge first, widens only the touch window to
reach the established barrier while keeping confirmation recent, and applies the
touch, wick-rejection (a Grade-A grab waives both minimums), accepted-close and
room gates and the shared reaction. The first edge that passes all of that is
the decision: if it then fails the confluence floor the other edge is not tried.
The first target is the equilibrium and the second the opposing edge.

## Status and execution

- **Certification**: `LEGACY_PARITY_PROVEN` ([matrix](README.md#certification)).
- **Symbols**: all instruments.
- **Execution**: Trades on every instrument.
- **Arbitration**: opportunities on the same symbol and direction that share a
  thesis group, a structure or an entry corridor compete; the best by quality,
  confluence, structural quality and freshness trades and the rest are
  suppressed ([execution](../execution.md#same-thesis-arbitration)).
