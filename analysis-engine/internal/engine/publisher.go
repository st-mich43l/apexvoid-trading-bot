package engine

import (
	"context"
	"sync"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// publishRetryBackoff is how long OpportunityPublisher waits before
// retrying a failed publish — a fixed, documented constant (no
// exponential-backoff library; this module's established preference is
// to reach for a dependency only when it earns its place), matching
// pingTimeout's own precedent in internal/transport/kafka/producer.go.
const publishRetryBackoff = 2 * time.Second

// OpportunityKafkaClient is the minimal Kafka surface OpportunityPublisher
// needs — the same "a clean interface between what gets published and
// how Kafka does it" separation ctrader-engine's own IMarketEventPublisher
// already established for the .NET side (docs/adr's Kafka pipeline task).
// *kafka.Producer already implements this with zero changes to that
// package; a fake implementation lets this package's own tests exercise
// OpportunityPublisher without a real broker.
type OpportunityKafkaClient interface {
	PublishOpportunity(ctx context.Context, correlationID, causationID string, candidate opportunity.Candidate, algo kafka.AlgorithmVersion, occurredAt time.Time) error
	PublishOpportunityInvalidated(ctx context.Context, correlationID, causationID string, symbol market.Symbol, payload kafka.OpportunityInvalidatedPayload, occurredAt time.Time) error
}

// publishJob is one queued lifecycle transition awaiting Kafka delivery.
type publishJob struct {
	symbol     market.Symbol
	algo       kafka.AlgorithmVersion
	transition opportunity.Transition
}

// OpportunityPublisher decouples opportunity lifecycle publication from
// SymbolWorker.ApplyWithResult's own hot path — source task §81 wires
// OpportunityBook -> Kafka Producer, but cmd/analysis-engine/main.go's own
// top-of-file doc comment already states the real constraint this design
// must honor: "a Kafka outage never becomes a candle-ingestion outage."
// Producer.PublishOpportunity/PublishOpportunityInvalidated are
// synchronous, network-blocking calls (ProduceSync); calling them
// directly from ApplyWithResult while holding SymbolWorker's own mutex
// would violate that constraint the first time Kafka is slow or
// unreachable. Enqueue is therefore a fast, lock-only append the
// ingestion path can always afford; actual Kafka I/O happens only in
// Run's own background goroutine.
//
// "Must never silently discard an opportunity" (main.go's own comment on
// the Producer it constructs) is honored by retrying a failed job
// indefinitely (at the front of the queue, preserving publish order for
// that symbol) rather than dropping it — the queue is a plain in-memory
// slice, not a durable outbox (Configuration V3/source task §45: the
// Analysis Engine does not necessarily own a PostgreSQL record for this),
// so a process restart during an outage is a real, documented limitation:
// still-queued events are lost, matching internal/opportunity.Book's own
// in-memory-only lifecycle state.
type OpportunityPublisher struct {
	mu     sync.Mutex
	queue  []publishJob
	notify chan struct{}

	client    OpportunityKafkaClient
	telemetry *telemetry.Recorder
}

// NewOpportunityPublisher returns nil (a valid, always-no-op receiver —
// see Enqueue/Run) when client is nil, matching this module's established
// "no-op if the optional dependency is absent" pattern (e.g. ctrader-
// engine's IMarketEventPublisher? marketPublisher = null). Callers
// holding a possibly-nil *kafka.Producer must not pass it directly —
// a nil *kafka.Producer stored in an OpportunityKafkaClient interface
// variable is NOT a nil interface (Go's classic "typed nil" trap), so
// the check below would never trigger; assign it to an
// OpportunityKafkaClient var first and leave that var unset when the
// concrete pointer is nil, as cmd/analysis-engine/main.go does.
func NewOpportunityPublisher(client OpportunityKafkaClient, recorder *telemetry.Recorder) *OpportunityPublisher {
	if client == nil {
		return nil
	}
	if recorder == nil {
		recorder = telemetry.NewRecorder()
	}
	return &OpportunityPublisher{client: client, telemetry: recorder, notify: make(chan struct{}, 1)}
}

// Enqueue records one lifecycle transition for background publication.
// Safe to call on a nil *OpportunityPublisher (Kafka disabled/absent) —
// callers never need their own nil check.
func (p *OpportunityPublisher) Enqueue(symbol market.Symbol, algo kafka.AlgorithmVersion, transition opportunity.Transition) {
	if p == nil || !transition.ShouldPublish() {
		return
	}
	p.mu.Lock()
	p.queue = append(p.queue, publishJob{symbol: symbol, algo: algo, transition: transition})
	p.mu.Unlock()
	p.telemetry.Count(telemetry.CounterOpportunityPublishEnqueued, string(symbol), "", 1)
	select {
	case p.notify <- struct{}{}:
	default:
	}
}

// Run drains the queue until ctx is cancelled — the one goroutine that
// ever touches Kafka for opportunity publication. Safe to call on a nil
// *OpportunityPublisher (returns immediately).
func (p *OpportunityPublisher) Run(ctx context.Context) {
	if p == nil {
		return
	}
	for {
		job, ok := p.peek()
		if !ok {
			select {
			case <-ctx.Done():
				return
			case <-p.notify:
				continue
			}
		}
		if err := p.publishOne(ctx, job); err != nil {
			p.telemetry.Count(telemetry.CounterOpportunityPublishFailed, string(job.symbol), "", 1)
			select {
			case <-ctx.Done():
				return
			case <-time.After(publishRetryBackoff):
			}
			continue // retry the SAME job (still at the front of the queue) — never drop it
		}
		p.telemetry.Count(telemetry.CounterOpportunityPublishSucceeded, string(job.symbol), "", 1)
		p.popFront()
	}
}

// peek returns the front job without removing it (removal only happens
// after a confirmed successful publish, in popFront).
func (p *OpportunityPublisher) peek() (publishJob, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.queue) == 0 {
		return publishJob{}, false
	}
	return p.queue[0], true
}

func (p *OpportunityPublisher) popFront() {
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.queue) == 0 {
		return
	}
	p.queue[0] = publishJob{} // release references for GC
	p.queue = p.queue[1:]
}

// publishOne dispatches job.transition.Kind to the correct Producer
// method — Created publishes analysis.opportunity.v1 (source task §81);
// Invalidated and Expired both publish analysis.opportunity.invalidated.v1
// (§83: no per-reason topic — the machine-readable reason code inside
// the payload already distinguishes them, ReasonSetupExpired vs. a
// strategy-owned reason). Every other Kind either never reaches here
// (ShouldPublish() already filtered at Enqueue) or has nothing to do.
func (p *OpportunityPublisher) publishOne(ctx context.Context, job publishJob) error {
	correlationID := kafka.NewEventID()
	switch job.transition.Kind {
	case opportunity.TransitionCreated:
		candidate := job.transition.Record.Candidate
		return p.client.PublishOpportunity(ctx, correlationID, "", candidate, job.algo, time.Unix(candidate.CreatedAt, 0))
	case opportunity.TransitionInvalidated, opportunity.TransitionExpired:
		record := job.transition.Record
		var reason string
		var at int64
		if record.Terminal != nil {
			reason, at = string(record.Terminal.Reason), record.Terminal.At
		}
		payload := kafka.OpportunityInvalidatedPayload{
			OpportunityID: record.Candidate.ID, Symbol: string(record.Candidate.Symbol),
			Strategy: string(record.Candidate.Strategy), ReasonCode: reason, InvalidatedAt: at,
		}
		return p.client.PublishOpportunityInvalidated(ctx, correlationID, "", record.Candidate.Symbol, payload, time.Unix(at, 0))
	default:
		return nil
	}
}
