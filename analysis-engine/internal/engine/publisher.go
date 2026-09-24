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
	PublishOpportunity(ctx context.Context, eventID, correlationID, causationID string, candidate opportunity.Candidate, algo kafka.AlgorithmVersion, occurredAt time.Time) error
	PublishOpportunityInvalidated(ctx context.Context, eventID, correlationID, causationID string, symbol market.Symbol, payload kafka.OpportunityInvalidatedPayload, occurredAt time.Time) error
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
// "Must never silently discard an opportunity" is honored by retrying the
// same stable event ID at the front of a filesystem-backed outbox. Production
// mounts that ledger on a named volume, so pending jobs and acknowledged
// creation state survive process and container restarts. Kafka delivery is
// at-least-once: an acknowledgement followed by a local ledger-write failure
// can replay the same event ID, allowing consumers and the audit to dedupe it.
type OpportunityPublisher struct {
	mu     sync.Mutex
	notify chan struct{}
	store  *publicationStore

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
	publisher, _ := NewDurableOpportunityPublisher(client, recorder, "")
	return publisher
}

// NewDurableOpportunityPublisher restores a publication-aware lifecycle
// ledger and pending outbox from path. The empty path keeps the same in-memory
// behavior used by small unit tests; production supplies a persistent path.
func NewDurableOpportunityPublisher(client OpportunityKafkaClient, recorder *telemetry.Recorder, path string) (*OpportunityPublisher, error) {
	if client == nil {
		return nil, nil
	}
	if recorder == nil {
		recorder = telemetry.NewRecorder()
	}
	store, err := openPublicationStore(path)
	if err != nil {
		return nil, err
	}
	return &OpportunityPublisher{client: client, telemetry: recorder, notify: make(chan struct{}, 1), store: store}, nil
}

// Enqueue records one lifecycle transition for background publication.
// Safe to call on a nil *OpportunityPublisher (Kafka disabled/absent) —
// callers never need their own nil check.
func (p *OpportunityPublisher) Enqueue(symbol market.Symbol, algo kafka.AlgorithmVersion, transition opportunity.Transition) {
	p.Observe(symbol, algo, transition, true)
}

// Observe records analytical lifecycle independently from transport. A
// terminal transition is only queued when its creation was acknowledged;
// otherwise ordering is retained in the durable ledger or the terminal is
// suppressed when the creation was never externally visible.
func (p *OpportunityPublisher) Observe(symbol market.Symbol, algo kafka.AlgorithmVersion, transition opportunity.Transition, publish bool) {
	if p == nil || !transition.ShouldPublish() {
		return
	}
	id := transition.Record.Candidate.ID
	if id == "" {
		return
	}
	p.mu.Lock()
	record := p.store.ledger.Records[id]
	removeRecord := false
	switch transition.Kind {
	case opportunity.TransitionCreated:
		if record.Creation == publicationUnknown {
			if publish {
				record.Creation = publicationPending
				p.store.ledger.Queue = append(p.store.ledger.Queue, newPublishJob(symbol, algo, transition))
				p.telemetry.Count(telemetry.CounterOpportunityPublishEnqueued, string(symbol), "", 1)
			} else {
				record.Creation = publicationSuppressed
			}
		}
	case opportunity.TransitionInvalidated, opportunity.TransitionExpired:
		if record.Terminal == publicationUnknown {
			job := newPublishJob(symbol, algo, transition)
			switch {
			case !publish && record.Creation == publicationPublished:
				record.Terminal = publicationDeferred
				record.Deferred = &job
			case !publish || record.Creation == publicationUnknown || record.Creation == publicationSuppressed:
				record.Terminal = publicationSuppressed
				removeRecord = true
				p.telemetry.Count(telemetry.CounterOpportunityTerminalSuppressed, string(symbol), "", 1)
			default:
				record.Terminal = publicationPending
				p.store.ledger.Queue = append(p.store.ledger.Queue, job)
				p.telemetry.Count(telemetry.CounterOpportunityPublishEnqueued, string(symbol), "", 1)
			}
		}
	}
	if removeRecord {
		delete(p.store.ledger.Records, id)
	} else {
		p.store.ledger.Records[id] = record
	}
	if err := p.store.save(); err != nil {
		p.telemetry.Count(telemetry.CounterOpportunityOutboxPersistFailed, string(symbol), "", 1)
	}
	p.mu.Unlock()
	if !publish {
		p.telemetry.Count(telemetry.CounterOpportunityPublishSuppressed, string(symbol), "", 1)
	}
	select {
	case p.notify <- struct{}{}:
	default:
	}
}

// ResumeLive promotes terminal transitions recovered during bootstrap only
// after a live bar arrives. Bootstrap itself therefore emits zero events.
func (p *OpportunityPublisher) ResumeLive(symbol market.Symbol) {
	if p == nil {
		return
	}
	p.mu.Lock()
	changed := false
	for id, record := range p.store.ledger.Records {
		if record.Terminal != publicationDeferred || record.Deferred == nil || record.Deferred.Symbol != symbol {
			continue
		}
		p.store.ledger.Queue = append(p.store.ledger.Queue, *record.Deferred)
		record.Terminal = publicationPending
		record.Deferred = nil
		p.store.ledger.Records[id] = record
		changed = true
	}
	if changed {
		if err := p.store.save(); err != nil {
			p.telemetry.Count(telemetry.CounterOpportunityOutboxPersistFailed, string(symbol), "", 1)
		}
	}
	p.mu.Unlock()
	if changed {
		select {
		case p.notify <- struct{}{}:
		default:
		}
	}
}

func newPublishJob(symbol market.Symbol, algo kafka.AlgorithmVersion, transition opportunity.Transition) publishJob {
	return publishJob{EventID: kafka.NewEventID(), Symbol: symbol, Algorithm: algo, Transition: transition}
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
		if err := p.persist(); err != nil {
			p.telemetry.Count(telemetry.CounterOpportunityOutboxPersistFailed, string(job.Symbol), "", 1)
			select {
			case <-ctx.Done():
				return
			case <-time.After(publishRetryBackoff):
			}
			continue
		}
		p.telemetry.Count(telemetry.CounterOpportunityPublishAttempted, string(job.Symbol), "", 1)
		if err := p.publishOne(ctx, job); err != nil {
			p.telemetry.Count(telemetry.CounterOpportunityPublishFailed, string(job.Symbol), "", 1)
			p.telemetry.Count(telemetry.CounterOpportunityPublishRetried, string(job.Symbol), "", 1)
			select {
			case <-ctx.Done():
				return
			case <-time.After(publishRetryBackoff):
			}
			continue // retry the SAME job (still at the front of the queue) — never drop it
		}
		p.telemetry.Count(telemetry.CounterOpportunityPublishSucceeded, string(job.Symbol), "", 1)
		p.ackFront(job)
	}
}

// persist enforces write-ahead ordering: a job cannot reach Kafka until the
// current queue and lifecycle ledger have been durably saved. A filesystem
// failure therefore degrades publication, never candle ingestion.
func (p *OpportunityPublisher) persist() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.store.save()
}

// peek returns the front job without removing it (removal only happens
// after a confirmed successful publish, in popFront).
func (p *OpportunityPublisher) peek() (publishJob, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.store.ledger.Queue) == 0 {
		return publishJob{}, false
	}
	return p.store.ledger.Queue[0], true
}

func (p *OpportunityPublisher) ackFront(job publishJob) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.store.ledger.Queue) == 0 {
		return
	}
	id := job.Transition.Record.Candidate.ID
	record := p.store.ledger.Records[id]
	if job.Transition.Kind == opportunity.TransitionCreated {
		record.Creation = publicationPublished
		p.store.ledger.Records[id] = record
	} else {
		// Acknowledged terminal records no longer carry ordering state. Remove
		// them so the durable ledger is bounded by live opportunities and
		// pending jobs rather than by all historical opportunities forever.
		delete(p.store.ledger.Records, id)
	}
	p.store.ledger.Queue[0] = publishJob{}
	p.store.ledger.Queue = p.store.ledger.Queue[1:]
	if err := p.store.save(); err != nil {
		p.telemetry.Count(telemetry.CounterOpportunityOutboxPersistFailed, string(job.Symbol), "", 1)
	}
}

// publishOne dispatches job.transition.Kind to the correct Producer
// method — Created publishes analysis.opportunity.v1 (source task §81);
// Invalidated and Expired both publish analysis.opportunity.invalidated.v1
// (§83: no per-reason topic — the machine-readable reason code inside
// the payload already distinguishes them, ReasonSetupExpired vs. a
// strategy-owned reason). Every other Kind either never reaches here
// (ShouldPublish() already filtered at Enqueue) or has nothing to do.
func (p *OpportunityPublisher) publishOne(ctx context.Context, job publishJob) error {
	correlationID := job.EventID
	switch job.Transition.Kind {
	case opportunity.TransitionCreated:
		candidate := job.Transition.Record.Candidate
		return p.client.PublishOpportunity(ctx, job.EventID, correlationID, "", candidate, job.Algorithm, time.Unix(candidate.CreatedAt, 0))
	case opportunity.TransitionInvalidated, opportunity.TransitionExpired:
		record := job.Transition.Record
		var reason string
		var at int64
		if record.Terminal != nil {
			reason, at = string(record.Terminal.Reason), record.Terminal.At
		}
		payload := kafka.OpportunityInvalidatedPayload{
			OpportunityID: record.Candidate.ID, Symbol: string(record.Candidate.Symbol),
			Strategy: string(record.Candidate.Strategy), ReasonCode: reason, InvalidatedAt: at,
		}
		return p.client.PublishOpportunityInvalidated(ctx, job.EventID, correlationID, "", record.Candidate.Symbol, payload, time.Unix(at, 0))
	default:
		return nil
	}
}
