# Opportunity Lifecycle V2

Phase S5 makes `internal/opportunity` the canonical, in-memory lifecycle for
technical opportunities produced for one symbol. It answers whether a market
thesis still exists; it does not make an execution or account decision.

## Identity

An opportunity ID is a lifecycle identity, not a Kafka event ID. A strategy
may call `DeterministicID` with its strategy ID/version, symbol, direction,
and a strategy-owned canonical `SetupKey` such as origin-fact references.
The setup key must not be an evaluation timestamp or Kafka event ID.

The `Book` rejects a reused ID when its strategy/version, symbol, direction,
entry geometry, invalidation geometry, or creation time disagree. This keeps
an accidental collision from silently overwriting a published thesis.

## States and transitions

```text
first observation      → Created      (future S9 publishes opportunity)
next observation       → Active       (no publish)
later observation      → Duplicate    (suppressed; no publish)
technical invalidation → Invalidated  (future S9 publishes once)
technical expiry       → Expired      (future S9 publishes once)
```

Only `Created`, `Invalidated`, and `Expired` report `ShouldPublish()`. The
Book deliberately does not infer invalidation from a candidate being absent
from one evaluation: a strategy must provide a technical invalidation reason,
or its own `ExpiresAt` deadline must pass.

## Technical terminal reasons

Reasons are uppercase, underscore-delimited codes. Common codes include:

- `STRUCTURE_INVALIDATED`
- `ZONE_INVALIDATED`
- `LIQUIDITY_OBJECTIVE_CONSUMED`
- `RETEST_FAILED`
- `SETUP_EXPIRED`
- `OPPOSITE_DISPLACEMENT`
- `SESSION_EXPIRED`
- `INVALIDATION_PRICE_TRADED_THROUGH`

Strategies may define additional machine-readable technical codes. Account
loss limits, broker rejection, position sizing, and an Algo Bot execution-age
policy are not valid reasons here.

## Boundaries and next phases

The Book is transport-free and is serialized by the existing per-symbol
`SymbolWorker`. It retains terminal records for replay/audit and projects only
Created/Active candidates into `AnalysisSnapshot`.

S6 will evaluate enabled strategies and feed their candidates into the Book.
S9 will turn the Book's publishable transitions into the existing neutral
Kafka `analysis.opportunity.v1` and `analysis.opportunity.invalidated.v1`
events. Strategies themselves never import Kafka or publish directly.
