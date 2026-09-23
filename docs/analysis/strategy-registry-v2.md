# Strategy Registry V2 (Phase S6)

`analysis-engine/internal/strategy` now owns the behavior-free registry and
dependency-aware evaluator that sit between canonical `MarketContext` and
independent strategy implementations.

## Configuration contract

`config/analysis.yml` declares every semantic V2 strategy as:

```yaml
analysis:
  strategies:
    key_level:
      version: v2
      enabled: false
```

The complete configured catalog has 19 entries: the 18 independent strategies
locked by the Phase S2 audit, plus the one permitted compositional exception,
`confluence_zone`. The registry rejects an unknown ID, duplicate ID, omitted
approved ID, missing version, or an enabled ID that has no registered concrete
factory. There are no generic enable defaults and no legacy strategy-family
switches.

All entries are deliberately disabled at Phase S6. That is not a production
feature toggle: no Phase S7 strategy implementation has yet established its
own technical thesis. Enabling one before its factory lands fails startup
rather than silently producing no signals.

Each concrete factory receives only its own `strategy.Config` (`id`, `version`,
`enabled`, and its raw strategy-local parameters). It must parse and validate
its technical thresholds itself. The registry never supplies generic entry,
confirmation, stop, target, expiry, or quality behavior.

## Closed-bar dependency evaluation

Every `Strategy` declares `RequiredTimeframes()`.

```text
M1 closes  → evaluate enabled strategies that require M1
M5 closes  → evaluate enabled strategies that require M5
H1 closes  → evaluate enabled strategies that require H1
```

A strategy runs only when the just-closed timeframe is one of its declared
dependencies and canonical context exists for *all* of those dependencies. For
example, an M5+H1 strategy is deferred during M5 bootstrap until H1 is present;
it then evaluates on either an M5 or H1 close. This avoids scanning every
strategy on every bar and does not flatten multi-timeframe context.

The evaluator validates each returned candidate against the configured strategy
ID/version, current symbol, and the Phase S5 `Candidate` contract. It does not
observe the `OpportunityBook` and does not publish Kafka events. Phase S8 wires
evaluation into the per-symbol engine and lifecycle; Phase S9 owns publication.

## Explicit non-goals

- No individual strategy package or technical setup thesis (Phase S7).
- No engine invocation or opportunity lifecycle transition (Phase S8).
- No Kafka output or Algo Bot behavior (Phase S9).
- No migration of legacy `auto_algo.strategies.*` decision/risk settings;
  Phase S7 moves only strategy-owned technical settings once an actual strategy
  consumes them.
