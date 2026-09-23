# S11 Production Shadow Run — 2026-09-24

## Safety boundary

Analysis Engine V2 runs on the production market-data path:

```text
cTrader closed bars → Redis → Analysis Engine V2 → Kafka opportunity topics
```

There is no Algo Bot consumer group for either `analysis.opportunity.v1` or
`analysis.opportunity.invalidated.v1`. The Go output therefore cannot create a
TradePlan, place an order, alter a Python setup, or affect the live Python
technical authority. This is a real shadow run, not a paper-trading shortcut.

## Observed production state

Read-only inspection on 2026-09-24 found:

| Component | Result |
|---|---|
| `apexvoid-analysis-engine` | healthy |
| `apexvoid-trading-redis` | healthy |
| `apexvoid-kafka` | healthy |
| `analysis.opportunity.v1` retained offsets | 1,639 records across non-empty partitions |
| `analysis.opportunity.invalidated.v1` retained offsets | 1,572 records across non-empty partitions |
| Kafka consumer groups | none |

Sample retained records were schema-shaped V2 opportunities for GBPUSD from
`demand`, `session_level`, and `supply`, with V2 structure/liquidity versions,
machine-readable evidence, technical entry/invalidation/target geometry, and
no account or execution fields.

## Bootstrap correction

The initial inspection also found a material measurement issue: engine startup
replayed retained Redis history and published candidates that were historical at
the time of production. Those records are valid reconstruction output, but they
are not new live signals and must not count toward shadow frequency or future
consumer eligibility.

S11 corrects this at the engine boundary. A `BarEvent` now carries origin:

| Origin | Builds technical state | Publishes lifecycle event |
|---|---:|---:|
| `bootstrap` | yes | no |
| `replay` | yes | no |
| `live` / recovered new bar | yes | yes |

The regression test drives the real 300-bar XAU M5 fixture as bootstrap input,
asserts that it produces live in-memory opportunities, and asserts that a
running Kafka publisher receives zero events. This preserves restart recovery
while making post-deploy Kafka records suitable for a real-time shadow audit.

## Shadow acceptance criteria

After deploying the correction, observe at least multiple market sessions over
XAU, one non-JPY FX pair, and one JPY pair. For each received V2 event record:

- `produced_at` should be contemporaneous with `occurred_at`; old historical
  setup timestamps must not appear immediately after an engine restart.
- `event_type`, payload shape, configuration fingerprint, strategy/version,
  and machine-readable evidence must be valid.
- Kafka consumption must remain absent from the execution path; no TradePlan or
  order may be produced from a V2 shadow event.
- Candidate frequency, duplicate rate, invalidation/expiry rate, MFE/MAE, and
  eventual target reach need comparison against the existing Python path before
  any policy cutover.

S11 is intentionally not a declaration that V2 is ready to replace Python.
The missing comparison window and explicit cutover approval remain the gates
for S12.
