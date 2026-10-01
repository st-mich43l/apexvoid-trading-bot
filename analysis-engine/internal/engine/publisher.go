package engine

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/arbitration"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// log is package-level rather than a constructor parameter so the 16+
// existing NewOpportunityPublisher/NewDurableOpportunityPublisher call
// sites (production and test) never need to change. Tests get a silent
// discard logger by default; cmd/analysis-engine/main.go calls SetLogger
// once at startup to install the real structured logger.
var log = slog.New(slog.NewTextHandler(io.Discard, nil))

// SetLogger installs the logger opportunity publication events are
// recorded through. Nil is ignored so a misordered or absent call can
// never leave publisher logging pointed at a nil logger.
func SetLogger(l *slog.Logger) {
	if l != nil {
		log = l
	}
}

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
	PublishRecoveredOpportunity(ctx context.Context, eventID, correlationID, causationID string, candidate opportunity.Candidate, algo kafka.AlgorithmVersion, occurredAt time.Time, recoveredAt time.Time) error
	PublishOpportunityInvalidated(ctx context.Context, eventID, correlationID, causationID string, symbol market.Symbol, payload kafka.OpportunityInvalidatedPayload, occurredAt time.Time) error
	PublishArbitrationDecision(ctx context.Context, eventID, correlationID, causationID string, symbol market.Symbol, payload kafka.ArbitrationDecisionPayload, occurredAt time.Time) error
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

// EnqueueArbitrationDecision records one Phase 2 arbitration.Decision for
// background publication. Unlike Enqueue/Observe, an arbitration decision
// carries no creation/terminal ledger bookkeeping: it is a current-status
// projection, not a once-only lifecycle fact, so republishing it is
// harmless and this appends directly to the durable queue rather than
// consulting p.store.ledger.Records. Safe to call on a nil
// *OpportunityPublisher (Kafka disabled/absent).
func (p *OpportunityPublisher) EnqueueArbitrationDecision(symbol market.Symbol, opportunityID string, decision arbitration.Decision, decidedAt int64) {
	if p == nil {
		return
	}
	p.mu.Lock()
	p.store.ledger.Queue = append(p.store.ledger.Queue, publishJob{
		EventID: kafka.NewEventID(), Symbol: symbol,
		Arbitration: &arbitrationJob{OpportunityID: opportunityID, Decision: decision, DecidedAt: decidedAt},
	})
	p.telemetry.Count(telemetry.CounterOpportunityPublishEnqueued, string(symbol), "", 1)
	if err := p.store.save(); err != nil {
		p.telemetry.Count(telemetry.CounterOpportunityOutboxPersistFailed, string(symbol), "", 1)
	}
	p.mu.Unlock()
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

// BackfillLive promotes the current live candidates recovered during Redis
// bootstrap into lifecycle creation events. Bootstrap deliberately suppresses
// historical publication, so a normal Observe(Created, publish=true) would
// incorrectly remain blocked by the durable "suppressed" ledger state. This
// method is the explicit, idempotent promotion point and marks each event as
// recovered so Algo Bot can distinguish it from a normal old Kafka replay.
func (p *OpportunityPublisher) BackfillLive(symbol market.Symbol, algo kafka.AlgorithmVersion, candidates []opportunity.Candidate, recoveredAt time.Time) {
	if p == nil || len(candidates) == 0 {
		return
	}
	if recoveredAt.IsZero() {
		recoveredAt = time.Now().UTC()
	}
	recoveredUnix := recoveredAt.Unix()
	p.mu.Lock()
	changed := false
	for _, candidate := range candidates {
		id := candidate.ID
		if id == "" {
			continue
		}
		record := p.store.ledger.Records[id]
		if record.Creation == publicationPending || record.Creation == publicationPublished || record.Terminal != publicationUnknown {
			continue
		}
		record.Creation = publicationPending
		transition := opportunity.Transition{
			Kind:   opportunity.TransitionCreated,
			Record: opportunity.Record{Candidate: candidate, State: opportunity.StateCreated},
		}
		p.store.ledger.Queue = append(p.store.ledger.Queue, publishJob{
			EventID: kafka.NewEventID(), Symbol: symbol, Algorithm: algo,
			Transition: transition, RecoveredAt: recoveredUnix,
		})
		p.store.ledger.Records[id] = record
		p.telemetry.Count(telemetry.CounterOpportunityPublishEnqueued, string(symbol), "recovered", 1)
		changed = true
	}
	if changed {
		if err := p.store.save(); err != nil {
			p.telemetry.Count(telemetry.CounterOpportunityOutboxPersistFailed, string(symbol), "recovered", 1)
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
			if job.Arbitration != nil {
				log.Warn("arbitration decision publish failed, retrying",
					"symbol", job.Symbol, "opportunity_id", job.Arbitration.OpportunityID,
					"status", job.Arbitration.Decision.Status, "error", err,
				)
			} else {
				log.Warn("opportunity publish failed, retrying",
					"symbol", job.Symbol, "strategy", job.Transition.Record.Candidate.Strategy,
					"opportunity_id", job.Transition.Record.Candidate.ID,
					"transition", string(job.Transition.Kind), "error", err,
				)
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(publishRetryBackoff):
			}
			continue // retry the SAME job (still at the front of the queue) — never drop it
		}
		p.telemetry.Count(telemetry.CounterOpportunityPublishSucceeded, string(job.Symbol), "", 1)
		// The one place every successful opportunity lifecycle publish
		// (any strategy, any symbol) passes through - see this file's own
		// "one goroutine that ever touches Kafka" doc comment above. Info
		// level, not Debug: this is the exact per-opportunity record that
		// was missing during the 2026-09-29 incident, when diagnosing what
		// the engine had actually published required reading Algo Bot's
		// consumer-side logs instead of this service's own.
		if job.Arbitration != nil {
			log.Info("arbitration decision published",
				"symbol", job.Symbol, "opportunity_id", job.Arbitration.OpportunityID,
				"status", job.Arbitration.Decision.Status, "reason_code", job.Arbitration.Decision.ReasonCode,
			)
		} else {
			candidate := job.Transition.Record.Candidate
			log.Info("opportunity published",
				"symbol", job.Symbol, "strategy", candidate.Strategy, "direction", candidate.Direction,
				"opportunity_id", candidate.ID, "transition", string(job.Transition.Kind),
			)
		}
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
	// Arbitration jobs carry no creation/terminal ledger record to update —
	// see EnqueueArbitrationDecision's own doc comment.
	if job.Arbitration == nil {
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
// (ShouldPublish() already filtered at Enqueue) or has nothing to do. A
// Phase 2 arbitration job (job.Arbitration set) publishes
// analysis.opportunity.arbitration.v1 instead, independent of Transition.
func (p *OpportunityPublisher) publishOne(ctx context.Context, job publishJob) error {
	correlationID := job.EventID
	if job.Arbitration != nil {
		a := job.Arbitration
		payload := kafka.ArbitrationDecisionPayload{
			OpportunityID: a.OpportunityID, Symbol: string(job.Symbol),
			Status: string(a.Decision.Status), ReasonCode: a.Decision.ReasonCode,
			ConflictingWith: a.Decision.ConflictingWith,
			ThesisID:        a.Decision.ThesisID, MergedWith: a.Decision.MergedWith,
			DecidedAt: a.DecidedAt,
		}
		return p.client.PublishArbitrationDecision(ctx, job.EventID, correlationID, "", job.Symbol, payload, time.Unix(a.DecidedAt, 0))
	}
	switch job.Transition.Kind {
	case opportunity.TransitionCreated:
		candidate := job.Transition.Record.Candidate
		if job.RecoveredAt != 0 {
			return p.client.PublishRecoveredOpportunity(ctx, job.EventID, correlationID, "", candidate, job.Algorithm, time.Now().UTC(), time.Unix(job.RecoveredAt, 0))
		}
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
