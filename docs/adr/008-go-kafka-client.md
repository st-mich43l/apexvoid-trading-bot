# ADR-008: Go Kafka client — `twmb/franz-go`

## Status
Accepted; implemented (Analysis Engine — Kafka transport task).

## Context
ADR-004 named Kafka the target durable inter-service event boundary but
deliberately implemented nothing — `internal/transport/kafka/` was a
`doc.go` skeleton with no client dependency. This task implements the
real Go Kafka producer/consumer, which means standardizing on exactly
one Kafka client library (source task §6: "do not introduce multiple
Kafka client libraries").

## Decision
`github.com/twmb/franz-go`, pinned at **v1.19.5** (not `@latest`).

### Why this client
- **Pure Go, no cgo.** The other mature option, `confluent-kafka-go`,
  wraps `librdkafka` via cgo — a C dependency, a second toolchain
  requirement for every build/CI image, and a worse fit for this
  module's demonstrated stdlib-first posture (`go.mod` had exactly one
  dependency, `yaml.v3`, before this task). `segmentio/kafka-go` is pure
  Go too but has materially weaker consumer-group and transactional
  support and is not under active development at the level franz-go is.
- **Idiomatic, low-abstraction API.** franz-go exposes `kgo.Client` with
  explicit `PollFetches`/`ProduceSync`/manual offset commits — it does
  not hide delivery semantics behind a callback-only or channel-only
  model, which is exactly what source task §18/§21 need (explicit
  control over when an offset commits, explicit delivery-error
  propagation from a produce call).
- **First-class consumer-group support**, including cooperative-sticky
  rebalancing and manual commit control (`kgo.DisableAutoCommit`,
  `kgo.BlockRebalanceOnPoll`) — needed for §37/§46 (safe rebalance, no
  work permanently pinned to a partition).

### The one concrete incompatibility found, and how it's resolved
franz-go's own `go.mod` floor climbs fast: `@latest` (v1.22.0, at the
time of this task) requires **Go >= 1.26.0**; v1.20.x requires **Go >=
1.24.0**. This repo's `analysis-engine/go.mod` targets `go 1.23`, and
every verification command in this repo's own established workflow runs
inside `golang:1.23-alpine` (no newer Go toolchain is installed on the
host, and this task did not upgrade the whole module's target Go version
purely to chase a client library's latest tag).

**v1.19.5** is the newest franz-go release whose own `go.mod` floor
(`go 1.23.8`) is satisfied by the toolchain actually available
(`golang:1.23-alpine` resolves to Go 1.23.12). Pinning here — rather
than floating `@latest` — is the correct outcome anyway per source task
§6's own instruction to *choose one client and standardize on it*, not
an accidental downgrade: `go.mod`'s `go` directive was bumped from
`1.23` to `1.23.8` (with an explicit `toolchain go1.23.12` line) to
satisfy franz-go's own stated floor, still fully served by the same
Docker image this whole repo's Go verification already runs in.

### Producer behavior
- `kgo.Client` used purely as a producer (no `kgo.ConsumerGroup` option)
  for `Producer` (`producer.go`).
- **Idempotent by default**: franz-go enables the idempotent producer
  (`enable.idempotence`-equivalent) automatically unless explicitly
  disabled — satisfies source task §22 without extra configuration.
  Required acks left at franz-go's default (`AllISRAcks`, i.e. `acks=all`)
  — source task §21's "do not lower durability for benchmark speed" is
  honored by *not overriding* this default downward.
- `client.ProduceSync(ctx, record)` (not the async/callback `Produce`)
  is used for every publish — it blocks until the broker acknowledges
  (or `ctx` is cancelled) and returns a `ProduceResults` whose errors are
  returned directly to the caller, satisfying §21's "no fire-and-forget
  API where errors disappear."

### Consumer-group support
`Consumer` (`consumer.go`) creates its own `kgo.Client` with
`kgo.ConsumerGroup(group)`, `kgo.ConsumeTopics(...)`, and
`kgo.DisableAutoCommit()`. Offsets are committed explicitly, per record,
only after that record's handler has returned successfully or been
finalized as a permanent failure (§18) — never on a fixed timer, never
before processing.

### Shutdown model
`Consumer.Run(ctx)` treats `ctx` cancellation as the sole shutdown
signal: the poll loop exits on the next `PollFetches` return (franz-go
returns promptly on context cancellation), no new record is dispatched
to the handler after that point, and `Close` flushes/closes the
underlying `kgo.Client` with a bounded timeout. `Producer.Close(ctx)`
calls `client.Flush(ctx)` before closing, so an in-flight
`ProduceSync` is not silently dropped. See §37 and `test/kafka/shutdown_test.go`.

### Retry model
franz-go retries broker/network-level failures (leader-not-available,
timeout, etc.) internally per its own configured retry backoff — this is
transport-level and not re-implemented. **Application-level** handler
retry (a transient `Handler.Handle` failure) is separate, bounded, and
implemented in `consumer.go` — see ADR-009 and `docs/transport/kafka.md`
for the exact policy. The two are never conflated.

### Testing approach
- **Fast, deterministic, broker-free** (`test/kafka/codec_test.go`,
  `envelope_test.go`, `key_test.go`, `idempotency_test.go`): pure
  encode/decode/key-selection/history-append logic, no network, run in
  every normal `go test ./...`.
- **Real-broker integration** (`test/integration/kafka_pipeline_test.go`):
  produces and consumes against a real, ephemeral Redpanda container
  (Kafka-protocol-compatible; source task §51's own preferred option) —
  not a hand-rolled fake that would only prove this code agrees with
  itself. Skips cleanly (`t.Skip`) when no broker is reachable, so the
  mandatory `go test ./...` never requires Docker; the broker-backed run
  is a separate, explicitly documented command (`docs/transport/kafka.md`).
- No second Kafka client library was added anywhere, including for
  tests — the same `kgo.Client` code path is exercised by both the
  production code and the integration test.

## Consequences
- One client dependency (`github.com/twmb/franz-go`, plus its own
  transitive `klauspost/compress`, `pierrec/lz4/v4`,
  `twmb/franz-go/pkg/kmsg`, `golang.org/x/crypto`) enters `go.mod`. This
  is the second and third real dependency this module has ever taken on
  (after `yaml.v3`), consistent with this project's "add a dependency
  only when it carries real weight" posture — a hand-rolled Kafka wire
  protocol client is not a reasonable scope for this task.
- `github.com/santhosh-tekuri/jsonschema/v5` (v5.3.1, pure Go, zero
  further dependencies) is also added — used **only** from
  `test/contracts/`, never from production code (source task §59: "keep
  a JSON-Schema validator out of the hot path unless runtime validation
  is deliberately desired" — it is not desired here; contract tests
  prove the Go DTOs and the JSON Schemas agree, at test time only).
- A future upgrade to franz-go `@latest` requires first upgrading this
  module's target Go version (and therefore the Docker image every
  verification command in this repo uses) — tracked, not silently
  blocked; not done by this task.
