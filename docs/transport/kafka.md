# Kafka Transport (analysis-engine)

## Live pipeline status

The first real transport path is now wired end to end:

`ctrader-engine` → `market.bar.closed.v1` → `analysis-engine` → existing
engine dispatch path.

The cTrader producer awaits the broker acknowledgement before writing the
same close to Redis. Redis remains the bar cache and legacy compatibility
surface; Kafka is the durable event bus. Startup full-window history is
written to Redis without replaying Kafka, while reconnect incremental
catch-up is published in close-time order. Duplicate delivery remains an
accepted at-least-once condition and is handled by the consumer/engine path.

`kafka-init` creates the four V3-configured topics and verifies partition,
replication, and retention settings. It must complete before either the
producer or analysis engine starts. The analysis engine exposes
`/health/live` and `/health/ready`; readiness is false until the configured
Kafka broker is reachable and the consumer loop is running.

Status: implemented (`analysis-engine/internal/transport/kafka`). This
document must always describe the same behavior the code does — where
they'd disagree, the code and its tests (`analysis-engine/test/kafka/`,
`test/integration/kafka_pipeline_test.go`, `test/contracts/`) are the
actual truth.

Kafka is transport. It never contains technical-analysis logic — see
[`../architecture/dependency-rules.md`](../architecture/dependency-rules.md)'s
third amendment for exactly how that's enforced at the dependency-graph
level, not just asserted here.

## Topics

Analysis-engine consumes:

```text
market.bar.closed.v1   (always)
market.tick.v1          (only when transport.kafka.tick_consumption_enabled=true — off by default, no producer exists yet)
```

Analysis-engine publishes:

```text
analysis.opportunity.v1
analysis.opportunity.invalidated.v1
```

No topic is ever created for an internal calculation (`analysis.atr`,
`analysis.swing`, `analysis.bos`, `analysis.fvg`, ...) — those never
leave a symbol's own `state.SymbolState`. The publisher abstraction
(`kafka.Producer`) is built so `analysis.snapshot.v1` can be added later
as a third `Publish...` method without redesigning transport — it does
not exist yet.

## Producers and consumers

- **`kafka.Producer`** (`producer.go`) publishes
  `analysis.opportunity.v1` / `analysis.opportunity.invalidated.v1`,
  synchronously (`ProduceSync`), keyed by canonical symbol. **Nothing
  calls it in production yet** — no strategy exists to produce a real
  `opportunity.Candidate` from (Analysis Engine V2's own scope
  explicitly excluded strategies; that's the next task). Proven correct
  by `test/kafka/producer_test.go` against a real broker and
  `test/contracts/schema_test.go` against the real JSON schemas.
- **`kafka.Consumer`** (`consumer.go`) consumes `market.bar.closed.v1`
  (and, when enabled, `market.tick.v1`), decodes/validates/adapts each
  record, and calls a `kafka.Handler`. `internal/engine.KafkaHandler`
  (`internal/engine/kafka_handler.go`) is the real implementation —
  wired into `cmd/analysis-engine`'s composition root when
  `transport.kafka.enabled=true`.

## Pipeline (source task §25)

```text
Kafka record
  -> envelope decoder   (kafka.DecodeStrict into kafka.Envelope, kafka.Envelope.Validate)
  -> contract validation (kafka.DecodeStrict into the typed payload + its own semantic Validate)
  -> typed market event  (kafka.BarEventFromPayload -> marketdata.BarEvent — the adapter, adapter.go)
  -> engine               (engine.KafkaHandler.HandleBarClosed -> Engine.Dispatch, the exact same path cmd/replay and every other caller uses)
```

## Kafka record key (source task §14/§15)

Every market-domain record's Kafka key is the canonical symbol alone
(`kafka.RecordKey`) — **never** timeframe-qualified, never a random
event ID, never the broker symbol. `XAU` M1/M5/M15/H1 all use the
identical key `"XAU"`; `EURUSD` uses a different key and may land on a
different partition. This is mandatory, not a convention: it is what
makes "one symbol's events stay ordered, different symbols process
concurrently" hold at the transport layer, matching the frozen
per-symbol `SymbolWorker` architecture
([`../architecture/dependency-rules.md`](../architecture/dependency-rules.md)).
Proven in `test/kafka/key_test.go`.

## Bar identity (source task §16) — deliberately NOT open time

`kafka.BarIdentity{Symbol, Timeframe, CloseTime}` is the deterministic
logical identity used for deduplication — a **different** concept from
the record key above, and it uses **close time**, not open time. This is
a deliberate divergence from today's live Redis convention: `docs/redis-contract.md`
states plainly that the `bars:{SYMBOL}:{TF}` ZSET is "scored by the UTC
bar-open epoch seconds," and `market.Candle.Time` (the whole existing
Go domain model) mirrors that same open-time convention. The source
task's own §16 explicitly specifies **close** time for the *new*
Kafka-native bar identity concept, and that instruction was followed as
written rather than silently kept consistent with the older Redis
convention — the two systems are allowed to differ here because they
serve different purposes (a ZSET needs a stable sort key across the bar's
entire open lifetime; a closed-bar-only event identity only ever exists
after the bar has definitively closed). See
`contracts/market/bar-closed-v1.schema.json`'s own field-level comments.

## Ordering (source task §47)

> Kafka guarantees ordering within a partition. ApexVoid keys all market
> events by canonical symbol. Therefore all events for a symbol are
> expected to share partition ordering.

The consumer does **not** treat this as a substitute for its own causal
sequencing checks: `marketdata.TimeframeHistory.Append`'s existing
`AppendOutOfOrder` result (from the Analysis Engine V2 task) still fires
if a record somehow arrives with an out-of-order timestamp — transport
ordering is evidence, not proof.

## Partitions (source task §45)

Production topic partition count is **not** hardcoded to 1 anywhere in
this codebase, and this document does not assume "one partition per
symbol" is statically guaranteed — Kafka's own key-hash partitioning
distributes symbols across however many partitions the topic actually
has. Recommendation for a real deployment: partition count should be
chosen to comfortably exceed the number of concurrently-live symbols (5
today — XAU, EURUSD, GBPUSD, GBPJPY, USDJPY, per `config/instruments.yml`)
so cross-symbol concurrency isn't artificially bottlenecked; this is
the checked-in deployment contract: market bars/ticks use 6 partitions and
opportunity topics use 3, all with replication factor 1 for the single-node
KRaft broker.

## Delivery semantics: at-least-once + application idempotency

See [ADR-009](../adr/009-kafka-delivery-semantics.md) for the full
decision and reasoning. Summary:

- **No Kafka transactions.** Idempotent producer (franz-go default,
  [ADR-008](../adr/008-go-kafka-client.md)) + `acks=all` + application
  idempotency is the whole story.
- **Offset commit ordering** (source task §18) is enforced in
  `consumer.go`'s `processOne`: consume → decode → validate → route →
  handler accepts → **then** commit. A transient handler failure is
  never acknowledged as successful; the offset is never committed before
  that.

## Duplicate delivery handling (source task §17/§54)

At-least-once delivery means the same closed-bar identity can
legitimately arrive twice. This is handled **one layer down**, in the
domain (`internal/marketdata`), not by a Kafka-specific dedup table —
one canonical duplicate-detection implementation serves every ingestion
path (Kafka, `cmd/replay`, a future live feed), not three independently
drifting ones:

- **Same identity, identical payload** → `marketdata.AppendDuplicate` —
  safely ignored, counted under `telemetry.CounterDuplicateEvents`.
- **Same identity, DIFFERENT payload** → `marketdata.AppendConflict`
  (added by this task) — **not** silently folded into the ordinary
  duplicate case. Correction policy: the original, already-analyzed
  candle is retained (a conflicting payload never retroactively rewrites
  structure/liquidity state already computed from the original values —
  that would be a causality violation), and the conflict is counted
  under its own distinct counter, `telemetry.CounterConflictEvents`, so
  an operator can see it happened. See
  `analysis-engine/internal/marketdata/timeframe_history.go`'s own
  `Append` doc comment and `test/kafka/idempotency_test.go`.

`kafka.BarIdentityFromPayload`/`kafka.BarIdentity` exist to compute this
identity from a wire payload directly (useful for logging/tracing a
rejected payload even before a full `BarEventFromPayload` conversion),
but the actual duplicate/conflict *decision* is made by
`TimeframeHistory.Append`, once, for every ingestion path.

## Poison messages (source task §19)

Distinguished explicitly via `kafka.Permanent`/`kafka.Transient`
(`errors.go`) — never conflated:

- **Permanent** (envelope decode failure, envelope validation failure,
  payload decode/semantic-validation failure, an unregistered symbol):
  logged with full structured diagnostic (topic/partition/offset/event
  metadata — `logPermanentFailure`), counted, and **committed past** —
  never retried.
- **Transient**: bounded local retry (`defaultHandlerRetries=3`,
  `defaultHandlerBackoff=200ms`, exponential-ish per-attempt backoff) —
  interruptible by shutdown. If still failing after the bound, the
  offset is deliberately left **uncommitted** and `Consumer.Run` returns
  an error: silently skipping real, still-failing market data would be
  worse than visibly stalling. A restart (or an operator fix) redelivers
  it.

### DLQ decision — none built, by design, this task

**Decision, made explicitly rather than defaulted into**: no
`deadletter.analysis-engine.v1` topic (or similar) was created. A
permanent failure is logged (with full metadata) and metric-counted, not
routed to a separate topic. Reasoning: creating a new topic is an
infrastructure decision (source task §44's own preference:
"infrastructure/deployment creates topics explicitly," and this task
does not own deployment topology, §63). A DLQ topic is a legitimate
future addition — the classification (`IsPermanent`) and the exact place
it would be produced from (`processOne`'s permanent-failure branches)
already exist; adding the actual produce call is a small, well-scoped
follow-up once the topic-ownership question has a real answer, not a
gap silently left unaddressed.

## Consumer offset commits (source task §18/§55)

`kgo.DisableAutoCommit()` + explicit `client.CommitRecords(ctx, record)`
per record, only after that record's outcome (success or finalized
permanent failure) is known. Proven end to end in
`test/kafka/consumer_test.go` against a real broker: a successful
handler commits and is never redelivered on restart; a permanent failure
commits past itself without retrying; a transient failure that resolves
within budget commits; a transient failure that exhausts its budget
leaves the offset uncommitted and **is** redelivered on restart.

## Rebalancing (source task §46)

`kgo.BlockRebalanceOnPoll()` + an explicit `client.AllowRebalance()`
call after every poll batch (whether or not that batch stalled) — a
rebalance never interrupts mid-record processing, and a stalled batch
never blocks a rebalance forever. The analysis engine's ownership unit
is the **symbol** (`SymbolWorker`), not the partition — after a
reassignment or restart, a symbol's state is reconstructed from its own
bootstrap/history path, not tied to which partition happened to serve
it before.

## Event envelope (source task §8-§13)

`contracts/common/event-envelope-v1.schema.json` / `kafka.Envelope`.
Every field's meaning, and why each design choice was made, is
documented on the schema file itself — summary:

| Field | Purpose |
|---|---|
| `event_id` | UUIDv7 (source task §10), time-sortable, hand-rolled (no dependency) — never used for ordering/dedup by a consumer |
| `event_type` | The topic name — must match exactly (§32) |
| `event_version` | The payload contract version, redundant with but separate from the "vN" topic suffix |
| `occurred_at` / `produced_at` | Unix **seconds** — see "Timestamp standard" below |
| `producer` | Which service produced this event |
| `correlation_id` | End-to-end trace ID, propagated unchanged across a derived-event chain (§11) |
| `causation_id` | The immediate parent event's `event_id` (§12) — omitted for a root event |
| `config_version` / `config_fingerprint` | Which exact Configuration V3 document produced this event, when the payload reflects configured engine behavior (§13) — never a secret |
| `payload` | The event-type-specific body, validated against its own schema separately |

## Timestamp standard (source task §9) — Unix seconds, uniformly

**Decision: Unix seconds, everywhere — the envelope and every payload
field, with no exception.** The task's own recommendation leaned toward
milliseconds; this repo chose to retain seconds instead, for a
documented, compelling reason (the task's own permitted alternative):
`market.Candle.Time` and every structure/liquidity/context/engine
timestamp built during the Analysis Engine V2 task — thousands of lines,
dozens of passing tests — already use Unix-second `int64` throughout.
Switching to milliseconds now would force a blast-radius rewrite of
already-shipped, already-tested core analysis logic for a **transport**
task, in direct tension with this task's own §1 ("Kafka must never
become part of technical-analysis logic") and this project's established
practice of not touching already-correct code without cause. Bar-close
resolution (the coarsest case, M1) never needs sub-second precision;
ticks (the one case that plausibly would) have no live producer yet, so
no real system depends on sub-second resolution today. Every contract
file and `kafka.Envelope` document this decision; no field anywhere in
this transport mixes seconds and milliseconds.

## Contract versioning policy (source task §32)

Topic name and payload contract version stay coherent: `market.bar.closed.v1`
carries `event_version: 1`. A **breaking** payload change (removing a
required field, changing a field's meaning or type, narrowing a valid
value set) requires a new topic, `market.bar.closed.v2`, with its own
schema file — an already-adopted `v1` payload shape is never silently
mutated into an incompatible one. A **non-breaking** addition (a new
optional field, a widened enum) may be added to the existing `v1`
schema, but source task §34's decision below means a producer adding a
new optional field will be rejected by an unmigrated `v1` consumer until
that consumer is updated — this is treated as acceptable and expected in
this internal, controlled system (see below), not a silent
compatibility hazard.

## Unknown JSON fields (source task §34) — reject, deliberately

`kafka.DecodeStrict` (`codec.go`) uses `json.Decoder.DisallowUnknownFields`
for **both** the envelope and every payload. Chosen deliberately, not
defaulted into: this is an internal, controlled system (analysis-engine
today has zero live cross-service Kafka producers or consumers other
than itself) — rejecting unknown fields surfaces producer/consumer
schema drift immediately, during development, rather than silently
ignoring a field a consumer doesn't yet know about and only noticing the
drift much later. Revisit this decision if/when true independent
multi-team producer/consumer evolution makes forward-compatible
unknown-field tolerance more valuable than the early-drift-detection
this currently provides.

## Kafka headers (source task §35)

Minimal, four fields (`headers.go`): `event_type`, `event_version`,
`content_type` (`application/json`), `producer`. The JSON envelope
remains authoritative for everything — headers exist only so a
consumer-side filter could route or skip a record without a full JSON
decode; nothing in this codebase currently reads headers for decisions,
only the envelope body.

## Health (source task §36/§68)

`kafka.Health`/`kafka.Snapshot` (`health.go`) distinguish: configured,
connected/reachable, consumer running, producer ready, last successful
consume, last successful produce, last error (with timestamp).
`Snapshot.Ready()` implements the readiness-vs-liveness split (§68):
when Kafka is disabled, readiness never depends on it; when enabled, the
engine is only ready once transport is actually connected and usable —
never "ready" merely because the Go process is alive.

## Graceful shutdown (source task §37/§57)

- **Consumer**: `ctx` cancellation is the sole shutdown signal
  (`Consumer.Run`). No new record is dispatched to `Handler` once `ctx`
  is done; an in-flight retry-backoff wait is interrupted immediately;
  `client.Close()` runs via `defer`.
- **Producer**: `Producer.Close(ctx)` flushes any in-flight
  `ProduceSync` within `ctx`'s own deadline before closing the
  underlying client — a publish issued just before shutdown is not
  silently dropped.
- Proven in `test/kafka/shutdown_test.go` against a real broker,
  including a goroutine-leak smoke check across repeated start/stop
  cycles, run under `go test -race`.

## Backpressure (source task §48)

No unbounded Go channel or slice buffers fetched records — the consumer
processes one poll batch's records synchronously (respecting per-record
commit ordering) before polling again; if the engine handler is slow,
the consumer simply polls less often, and **Kafka's own retention** is
the buffer, exactly as source task §48 specifies. No custom queue was
built.

## Handler concurrency (source task §49)

Per-record processing inside one `Consumer.Run` call is sequential,
preserving the FIFO-per-symbol guarantee by construction: every
timeframe of one symbol shares one partition (the record-key rule
above), and Kafka delivers one partition's records in order within a
poll batch. No goroutine is spawned per record. Cross-symbol concurrency
comes from `internal/engine.Engine`'s own existing per-symbol
`SymbolWorker` locking (proven in `test/engine/worker_test.go`), which
`engine.KafkaHandler.HandleBarClosed` routes into unchanged — the Kafka
consumer does not need its own separate concurrency model layered on
top.

## Configuration (source task §4/§5/§70)

`config/transport.yml`'s `transport.kafka` section — real Configuration
V3, no hidden defaults, no topic name hardcoded in Go production code
(every topic name is read via `engine.KafkaConfigFromConfig`, the one
place `internal/config.Document` becomes `kafka.Config`). See that file
for the full shape and `kafka.Config.Validate`
(`internal/transport/kafka/config.go`) for the exact fail-closed rules:
no brokers, an empty broker address, a missing required topic, a missing
consumer group, duplicate logical topics, or an invalid client ID all
reject before any client is constructed.

**Checked-in default: `transport.kafka.enabled: true`.** The local and
production Compose templates provide the pinned internal KRaft broker;
`kafka-init` provisions the V3 topic contract before cTrader or
analysis-engine starts. The engine still fails closed when Kafka is enabled
but unreachable (§43).

## Security future-proofing (source task §69)

Nothing here hardwires plaintext-only assumptions: `kafka.Config`'s
brokers/client construction goes through franz-go's own `kgo.Opt`
mechanism, which supports TLS and SASL as additional options. Neither is
wired in — local/internal plaintext is the only mode this task
implements, matching today's actual deployment reality; adding unused
TLS/SASL configuration now would be exactly the kind of premature
"completeness" source task §69 itself warns against.

## Running the real-broker tests

`go test ./...` never requires Docker — every real-broker test in
`test/kafka/` and `test/integration/kafka_pipeline_test.go` skips
cleanly via `t.Skip` when `KAFKA_TEST_BROKERS` is unset. To run them for
real against an ephemeral Redpanda broker (the same broker this task's
own verification used):

```bash
docker network create apexvoid-kafka-test
docker run -d --name redpanda-test --network apexvoid-kafka-test \
  docker.redpanda.com/redpandadata/redpanda:latest \
  redpanda start --smp 1 --memory 512M --reserve-memory 0M --overprovisioned --node-id 0 \
  --check=false --kafka-addr PLAINTEXT://0.0.0.0:9092 \
  --advertise-kafka-addr PLAINTEXT://redpanda-test:9092

cd analysis-engine
docker run --rm --network apexvoid-kafka-test -v "$(pwd)/..":/src -w /src/analysis-engine \
  -e KAFKA_TEST_BROKERS=redpanda-test:9092 golang:1.23-alpine \
  sh -c "go test ./test/kafka/... ./test/integration/... -v -timeout 300s"

# under -race:
docker run --rm --network apexvoid-kafka-test -v "$(pwd)/..":/src -w /src/analysis-engine \
  -e KAFKA_TEST_BROKERS=redpanda-test:9092 -e CGO_ENABLED=1 golang:1.23-alpine \
  sh -c "apk add --no-cache gcc musl-dev >/dev/null 2>&1 && go test -race ./test/kafka/... -timeout 300s"

# teardown:
docker rm -f redpanda-test && docker network rm apexvoid-kafka-test
```

The tests create their own randomly-suffixed, isolated topics via
`kadm.CreateTopics` (Redpanda does not auto-create topics by default,
unlike some Kafka distributions) — nothing needs pre-provisioning.

## Independent strategy invariant (source task §72/§73)

Kafka transports **neutral analysis opportunities**. There are currently
zero V2 strategy implementations, and this transport code works without
knowing any strategy's name: `opportunity.StrategyID` is a plain string
carried through the neutral `opportunity.Candidate`/`OpportunityPayload`
shape. There is no `BreakoutRetestKafkaProducer`, no
`LiquiditySweepKafkaProducer`, and no strategy-specific topic — one
`kafka.Producer`, one `analysis.opportunity.v1` topic, for every
strategy that will ever exist. `internal/transport/kafka` does not
import `internal/strategy/<anything>` — it cannot: strategy sits at rank
6, this package at rank 7 ([`../architecture/dependency-rules.md`](../architecture/dependency-rules.md)),
and a rank-7 package importing a rank-6 one only flows the direction
already proven safe (kafka needs nothing from strategy; strategy is
structurally forbidden from importing kafka at all).
