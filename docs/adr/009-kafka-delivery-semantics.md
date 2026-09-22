# ADR-009: Kafka delivery semantics — at-least-once + application idempotency

## Status
Accepted; implemented (Analysis Engine — Kafka transport task).

## Context
Kafka's own delivery guarantees range from at-most-once (no retry, no
dedup) through at-least-once (retry, possible duplicates) to
exactly-once (transactions, `read_committed` isolation, more moving
parts and a real operational cost). Source task §18/§22/§23 ask for a
deliberate, documented choice, not a default arrived at by accident.

## Decision
**At-least-once delivery, with application-level idempotency absorbing
duplicates. No Kafka transactions.**

### Why not exactly-once
Source task §23 is explicit: "Do NOT introduce Kafka transactions/EOS
unless there is a demonstrated need... at-least-once + idempotent
processing is simpler and appropriate for the current architecture."
Concretely:
- There is exactly one producer-to-topic relationship active today
  (analysis-engine → `analysis.opportunity.v1` /
  `analysis.opportunity.invalidated.v1`) and one consumer-to-topic
  relationship (analysis-engine ← `market.bar.closed.v1` /
  `market.tick.v1`, tick disabled by default). Transactions earn their
  operational cost in a read-process-write pipeline spanning multiple
  topics atomically — that pipeline does not exist yet (no strategy
  publishes an opportunity derived from consuming another Kafka topic
  in the same transaction).
- The domain already has a natural idempotency key at every layer that
  matters (see below) — duplicates are cheap and safe to detect and
  discard without a broker-level transaction.

### At-least-once, in practice
- **Producer**: idempotent producer enabled (ADR-008), `acks=all`. A
  `ProduceSync` failure is surfaced to the caller, who may legitimately
  retry — a retried produce after an ambiguous failure can, in the
  worst case, still result in two records on the topic (idempotent
  producer prevents *broker-side* duplication from retries of the *same*
  producer session, not from an application-level re-publish after a
  timeout whose actual outcome was unknown). This is accepted, not
  worked around with transactions.
- **Consumer**: `kgo.DisableAutoCommit()` — an offset commits only after
  its record's handler has completed (source task §18's exact ordering:
  consume → decode → validate → route → engine accepts → commit). A
  crash between "engine accepted" and "offset committed" causes that
  record to be redelivered on restart — by design, not a bug to
  eliminate.

### Application idempotency: the real defense against duplicates
Three-part idempotency key hierarchy, each owned by the layer that can
actually detect a repeat:
1. **Kafka partition offset** — detects nothing on its own (offsets are
   positions, not content), but combined with `DisableAutoCommit`
   guarantees no record is skipped silently.
2. **Bar identity** (`kafka.BarIdentity{Symbol, Timeframe, CloseTime}`,
   source task §16) — `marketdata.TimeframeHistory.Append` already
   detects a repeat of the same identity and reports
   `AppendDuplicate` (identical payload, safely ignored) versus
   `AppendConflict` (same identity, different payload — a correction,
   never silently merged; see `docs/transport/kafka.md`). This existed
   before this task for other delivery paths (`cmd/replay`) and now also
   absorbs Kafka's at-least-once redelivery for free — no
   Kafka-specific dedup table was built, because the domain layer
   already has the correct one.
3. **Event ID** (UUIDv7, source task §10) — carried on every envelope
   for tracing/log correlation, explicitly **not** used for ordering or
   dedup (`docs/transport/kafka.md`'s "Event ID" section) — Kafka
   partition ordering and bar identity are authoritative for those.

### Record key = canonical symbol
Source task §14/§15: every market-domain Kafka record is keyed by
canonical symbol alone (`kafka.RecordKey`), never timeframe-qualified.
This is what makes "per-symbol FIFO, cross-symbol concurrency"
(the frozen `internal/engine` worker model) hold at the transport layer
too: every timeframe of one symbol shares a partition and therefore
Kafka's own ordering guarantee, while different symbols may land on
different partitions and process concurrently. The consumer still
validates timestamps on receipt (`docs/transport/kafka.md`'s "Ordering
assumption" section) — partition ordering is evidence, not a substitute
for the domain's own causal sequencing checks
(`marketdata.AppendOutOfOrder` already exists for exactly this).

## Consequences
- No transactional producer, no `read_committed` consumer isolation
  level, no two-phase commit across topics. Simpler operationally, at
  the cost of the caller needing to tolerate (not prevent) rare
  double-publishes on producer-side ambiguous failures — accepted
  above.
- If a future strategy genuinely needs atomic multi-topic
  read-process-write, that is a new ADR, not a silent extension of this
  one.
- This ADR's policy is enforced in code (`internal/transport/kafka/`)
  and proven in tests (`test/kafka/idempotency_test.go`,
  `test/kafka/consumer_test.go`'s commit-ordering tests), not only
  documented here.
