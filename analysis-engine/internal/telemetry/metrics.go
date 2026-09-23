// Package telemetry measures the engine, per source task §57:
// marketdata/indicator/structure/liquidity/zone/session/fib/keylevel/
// trendline/context/
// snapshot/event durations, event counts, queue age. Labels are symbol+timeframe+phase
// only — bounded sets (the live instrument list and the fixed timeframe
// enum), never a raw candidate ID or price (§57: "do not use unbounded
// metric labels").
//
// A foundational, dependency-free utility (docs/architecture/
// dependency-rules.md, rank 0 alongside internal/market): imports nothing
// internal itself, and — unlike internal/visualization — IS meant to be
// imported by higher-rank packages that need to record their own timing
// (internal/engine does exactly this).
package telemetry

import (
	"sync"
	"time"
)

// Phase names the pipeline stage being timed — source task §57's exact
// list.
type Phase string

const (
	PhaseMarketData Phase = "marketdata_update_ms"
	PhaseIndicator  Phase = "indicator_update_ms"
	PhaseStructure  Phase = "structure_update_ms"
	PhaseLiquidity  Phase = "liquidity_update_ms"
	PhaseZone       Phase = "zone_update_ms"
	PhaseTrendline  Phase = "trendline_update_ms"
	PhaseKeyLevel   Phase = "keylevel_update_ms"
	PhaseSession    Phase = "session_update_ms"
	PhaseFib        Phase = "fib_update_ms"
	PhaseContext    Phase = "context_update_ms"
	PhaseSnapshot   Phase = "snapshot_ms"
	PhaseEventTotal Phase = "event_total_ms"

	// PhaseStrategy and PhaseOpportunity are a Phase S8 amendment (Wire
	// engine strategy evaluation) to this originally-frozen list — the
	// same "one phase per pipeline step" convention every domain above
	// already follows. PhaseStrategy times internal/strategy.Registry.
	// Evaluate; PhaseOpportunity times feeding its resulting Candidates
	// through internal/opportunity.Book (Observe + Expire).
	PhaseStrategy    Phase = "strategy_evaluation_ms"
	PhaseOpportunity Phase = "opportunity_lifecycle_ms"
)

// Counter names an event-count metric — source task §57's "events
// processed, events rejected, duplicate events, out-of-order events" plus
// queue age (recorded as a duration via Record, not a Counter).
type Counter string

const (
	CounterEventsProcessed  Counter = "events_processed"
	CounterEventsRejected   Counter = "events_rejected"
	CounterDuplicateEvents  Counter = "duplicate_events"
	CounterOutOfOrderEvents Counter = "out_of_order_events"
	// CounterConflictEvents is a same-identity-different-payload bar
	// (marketdata.AppendConflict) — deliberately distinct from
	// CounterDuplicateEvents: a conflict is a
	// correction/data-quality event an operator should be able to see,
	// never silently folded into the ordinary/benign duplicate count.
	CounterConflictEvents Counter = "conflict_events"

	// Opportunity lifecycle transition counters — a Phase S8 amendment,
	// one counter per internal/opportunity.TransitionKind an operator can
	// observe (TransitionNoop is deliberately not counted: it carries no
	// new information by construction).
	CounterOpportunitiesCreated     Counter = "opportunities_created"
	CounterOpportunitiesActivated   Counter = "opportunities_activated"
	CounterOpportunitiesDuplicate   Counter = "opportunities_duplicate"
	CounterOpportunitiesInvalidated Counter = "opportunities_invalidated"
	CounterOpportunitiesExpired     Counter = "opportunities_expired"

	// Opportunity Kafka publication counters — a Phase S9 amendment.
	// Enqueued and (Succeeded + Failed-before-eventual-success) will not
	// match 1:1 during an outage: OpportunityPublisher retries a failed
	// job indefinitely rather than dropping it (see its own doc comment),
	// so one Enqueued job can contribute multiple Failed counts before
	// its own eventual Succeeded count.
	CounterOpportunityPublishEnqueued  Counter = "opportunity_publish_enqueued"
	CounterOpportunityPublishSucceeded Counter = "opportunity_publish_succeeded"
	CounterOpportunityPublishFailed    Counter = "opportunity_publish_failed"
	// CounterOpportunityPublishSuppressed is the S11 shadow-run audit
	// counter for lifecycle transitions reconstructed from bootstrap/replay
	// history. They remain in the in-memory OpportunityBook but are never
	// presented to consumers as a newly observed live opportunity.
	CounterOpportunityPublishSuppressed Counter = "opportunity_publish_suppressed_non_live"
)

// Kafka transport telemetry (kafka_consume_total, kafka_produce_total,
// consumer lag, etc. — source task §38) is deliberately NOT added to this
// Recorder: its dimensions are topic/event_type/result, not
// symbol/timeframe, and forcing them into this Recorder's label shape
// would either misuse the symbol/timeframe slots for something they
// don't mean or require widening this type's key for one caller.
// internal/transport/kafka/metrics.go implements its own small,
// purpose-built recorder using the identical pattern (sync.Mutex + map,
// no external library) for exactly that reason.

type key struct {
	label     string // Phase or Counter, as a string
	symbol    string
	timeframe string
}

// Recorder is a concurrency-safe in-process metrics store — deliberately
// no external metrics library (Prometheus/OpenTelemetry): this project's
// own established preference (analysis-engine/go.mod has exactly one
// dependency, yaml.v3) is to reach for a library only when it carries
// real weight, and a bounded-cardinality counter/duration map does not
// need one yet. Exporting Snapshot's content to an external system is a
// later, separate concern.
type Recorder struct {
	mu        sync.Mutex
	durations map[key]time.Duration
	durationN map[key]int64 // sample count per key, so Snapshot can report an average, not just the last value
	counts    map[key]int64
}

// NewRecorder returns an empty Recorder.
func NewRecorder() *Recorder {
	return &Recorder{
		durations: make(map[key]time.Duration),
		durationN: make(map[key]int64),
		counts:    make(map[key]int64),
	}
}

// Record adds one duration sample for phase/symbol/timeframe.
func (r *Recorder) Record(phase Phase, symbol, timeframe string, d time.Duration) {
	r.mu.Lock()
	defer r.mu.Unlock()
	k := key{label: string(phase), symbol: symbol, timeframe: timeframe}
	r.durations[k] += d
	r.durationN[k]++
}

// Time starts a duration measurement and returns a func to call when the
// phase completes — the usual `defer rec.Time(...)()` pattern.
func (r *Recorder) Time(phase Phase, symbol, timeframe string) func() {
	start := time.Now()
	return func() { r.Record(phase, symbol, timeframe, time.Since(start)) }
}

// Count increments a counter by n (n may be negative to correct a
// mis-count, though no caller in this codebase does that today).
func (r *Recorder) Count(counter Counter, symbol, timeframe string, n int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	k := key{label: string(counter), symbol: symbol, timeframe: timeframe}
	r.counts[k] += n
}

// DurationSample is one Snapshot row for a duration metric.
type DurationSample struct {
	Phase          string
	Symbol         string
	Timeframe      string
	Total, Average time.Duration
	Samples        int64
}

// CountSample is one Snapshot row for a counter metric.
type CountSample struct {
	Counter   string
	Symbol    string
	Timeframe string
	Count     int64
}

// Snapshot returns a point-in-time, read-only copy of everything recorded
// so far — for tests, benchmarks, and (later, not this task) a real
// export path.
func (r *Recorder) Snapshot() ([]DurationSample, []CountSample) {
	r.mu.Lock()
	defer r.mu.Unlock()
	durations := make([]DurationSample, 0, len(r.durations))
	for k, total := range r.durations {
		n := r.durationN[k]
		var avg time.Duration
		if n > 0 {
			avg = total / time.Duration(n)
		}
		durations = append(durations, DurationSample{
			Phase: k.label, Symbol: k.symbol, Timeframe: k.timeframe,
			Total: total, Average: avg, Samples: n,
		})
	}
	counts := make([]CountSample, 0, len(r.counts))
	for k, c := range r.counts {
		counts = append(counts, CountSample{
			Counter: k.label, Symbol: k.symbol, Timeframe: k.timeframe, Count: c,
		})
	}
	return durations, counts
}
