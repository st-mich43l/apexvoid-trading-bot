package kafka

import (
	"sync"
	"time"
)

// Counter names a Kafka transport count metric — source task §38's
// exact list.
type Counter string

const (
	CounterConsumeTotal Counter = "kafka_consume_total"
	CounterConsumeError Counter = "kafka_consume_error_total"
	CounterDecodeError  Counter = "kafka_decode_error_total"
	// CounterDuplicate is reserved for a future TRANSPORT-level duplicate
	// signal (e.g. a producer-side dedup cache). Today's duplicate
	// detection happens one layer down, in the domain
	// (marketdata.AppendDuplicate / AppendConflict, counted by
	// internal/telemetry.CounterDuplicateEvents /
	// CounterConflictEvents) — see docs/transport/kafka.md's "Duplicate
	// delivery handling." Defined here now so a later transport-level
	// dedup mechanism doesn't need a schema/label-shape change.
	CounterDuplicate    Counter = "kafka_duplicate_total"
	CounterHandlerError Counter = "kafka_handler_error_total"
	CounterProduceTotal Counter = "kafka_produce_total"
	CounterProduceError Counter = "kafka_produce_error_total"
)

// Phase names a Kafka transport duration/gauge metric.
type Phase string

const (
	// PhaseConsumerLag is recorded as time.Since(record.Timestamp) at
	// the moment a record is first seen — a real, broker-timestamp-
	// derived measurement of how far behind "now" this consumer
	// currently is, not a fabricated placeholder. Offset-based lag
	// (comparing consumed offset to the partition's high-water mark)
	// would need an admin-client round trip per sample; this
	// time-based proxy is computed from data already in hand on every
	// record and is the more commonly actionable signal operationally.
	PhaseConsumerLag     Phase = "kafka_consumer_lag"
	PhaseHandlerDuration Phase = "kafka_handler_duration_ms"
	PhaseProduceDuration Phase = "kafka_produce_duration_ms"
)

type key struct {
	label     string
	topic     string
	eventType string
}

// Metrics is a small, concurrency-safe in-process recorder — the same
// sync.Mutex+map pattern as internal/telemetry.Recorder, purpose-built
// for this package's own topic/event_type label dimensions (source task
// §38: "controlled labels only: topic, event_type, result — avoid
// event_id, unbounded symbol cardinality, price, offset as metric
// labels"). Kept separate from telemetry.Recorder rather than reusing
// it: that type's key shape is symbol+timeframe, a different dimension
// set than topic+event_type, and forcing one into the other's label
// slots would misuse what those slots mean — see
// internal/telemetry/metrics.go's own doc comment on this exact point.
type Metrics struct {
	mu        sync.Mutex
	durations map[key]time.Duration
	durationN map[key]int64
	counts    map[key]int64
}

// NewMetrics returns an empty Metrics.
func NewMetrics() *Metrics {
	return &Metrics{
		durations: make(map[key]time.Duration),
		durationN: make(map[key]int64),
		counts:    make(map[key]int64),
	}
}

// Count increments counter for topic/eventType by n.
func (m *Metrics) Count(counter Counter, topic, eventType string, n int64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	k := key{label: string(counter), topic: topic, eventType: eventType}
	m.counts[k] += n
}

// Record adds one duration/gauge sample for phase/topic/eventType.
func (m *Metrics) Record(phase Phase, topic, eventType string, d time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	k := key{label: string(phase), topic: topic, eventType: eventType}
	m.durations[k] += d
	m.durationN[k]++
}

// Time starts a duration measurement, returning a func to call on
// completion — the usual `defer m.Time(...)()` pattern.
func (m *Metrics) Time(phase Phase, topic, eventType string) func() {
	start := time.Now()
	return func() { m.Record(phase, topic, eventType, time.Since(start)) }
}

// DurationSample is one Snapshot row for a duration/gauge metric.
type DurationSample struct {
	Phase, Topic, EventType string
	Total, Average          time.Duration
	Samples                 int64
}

// CountSample is one Snapshot row for a count metric.
type CountSample struct {
	Counter, Topic, EventType string
	Count                     int64
}

// Snapshot returns a point-in-time, read-only copy of everything
// recorded so far.
func (m *Metrics) Snapshot() ([]DurationSample, []CountSample) {
	m.mu.Lock()
	defer m.mu.Unlock()
	durations := make([]DurationSample, 0, len(m.durations))
	for k, total := range m.durations {
		n := m.durationN[k]
		var avg time.Duration
		if n > 0 {
			avg = total / time.Duration(n)
		}
		durations = append(durations, DurationSample{
			Phase: k.label, Topic: k.topic, EventType: k.eventType,
			Total: total, Average: avg, Samples: n,
		})
	}
	counts := make([]CountSample, 0, len(m.counts))
	for k, c := range m.counts {
		counts = append(counts, CountSample{Counter: k.label, Topic: k.topic, EventType: k.eventType, Count: c})
	}
	return durations, counts
}
