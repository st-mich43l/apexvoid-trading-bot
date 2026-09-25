# S12 — Go Analysis Authority Integration

## Scope delivered: S12A and S12B

The Algo Bot now has a strict, durable receiving boundary for the Go Analysis
Engine's V1 Kafka events. Deployment defaults retain Python authority:

```yaml
analysis:
  technical_authority:
    mode: python
    consumer_enabled: false
```

No deployment of this change starts a Kafka consumer or changes a live trading
decision. `mode: go` is rejected at configuration load time; it requires the
separately approved S12D implementation.

## S12A: lifecycle foundation

- `analysis_client.models` validates topic/envelope agreement, expected
  producer, V1 version, time order, price geometry, directional stop/targets,
  quality and required evidence before policy sees an event.
- `analysis_client.repository` writes every accepted delivery to PostgreSQL
  before a Kafka offset may be committed. `event_id` is the delivery fence;
  `opportunity_id` is the lifecycle identity.
- Terminal events received before a creation create a terminal tombstone. A
  later creation cannot reactivate it. Replays are idempotent.
- Contract-invalid records are durably stored in
  `analysis_opportunity_rejections` before their offset is committed, so one
  poison record cannot stall the partition indefinitely.
- Consumer offsets are manually committed one record at a time after durable
  processing. Database failures leave the offset uncommitted for redelivery.

## S12B: policy shadow

In `go_shadow`, each accepted creation produces a durable `contract_gap`
decision. It does not call a TradePlan builder, Redis trade-plan publisher,
risk reservation, Telegram delivery, or broker path.

The current `analysis.opportunity.v1` contract does not contain the factual
inputs needed for safe parity with the legacy Python execution path:

1. current executable price and spread context;
2. ATR used for policy/geometry;
3. confluence components and score provenance;
4. source-structure geometry and structural context;
5. execution confirmation state; and
6. strategy routing/mode facts.

Those belong in a reviewed V2 contract extension. Python must not infer them by
running a second detector.

## S12C entry criteria

Enable `go_shadow` only in a dedicated configuration/deployment change after:

- broker/topic/group reachability is proven;
- consumer lag, rejected-record rate, lifecycle dispositions and shadow
  outcomes have dashboards/alerts;
- same-input Python/Go comparison is reconciled across XAU and FX; and
- the known producer publication latency is accounted for independently from
  technical `occurred_at` age.

Rollback is configuration-only: set `consumer_enabled: false` (or retain
`mode: python`) and redeploy. Existing ledger rows remain audit evidence;
there is no execution state to unwind.
