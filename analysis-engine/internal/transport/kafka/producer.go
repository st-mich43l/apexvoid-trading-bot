package kafka

import (
	"context"
	"fmt"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// pingTimeout bounds the startup broker-reachability check (source task
// §43) — a documented protocol constant, not a runtime-tunable setting;
// it changes nothing about behavior once connected.
const pingTimeout = 5 * time.Second

// ConfigProvenance answers "which exact ApexVoid configuration generated
// this opportunity?" (source task §13) — computed once at startup from
// the resolved Configuration V3 document by internal/engine's
// composition wiring (internal/engine/config.go), never recomputed per
// event.
type ConfigProvenance struct {
	Version     int
	Fingerprint string
}

// AlgorithmVersion mirrors engine.SnapshotVersion's two fields without
// importing internal/engine — this package is rank 7 and engine is rank
// 8; a rank-7 package must never import rank 8. The composition root
// converts an engine.SnapshotVersion into this at the call site.
type AlgorithmVersion struct {
	Structure string
	Liquidity string
}

// Producer publishes analysis.opportunity.v1 and
// analysis.opportunity.invalidated.v1 — the only two topics
// analysis-engine ever produces to (source task §3: no topic is ever
// created for an internal calculation). No strategy exists yet, so
// nothing in this codebase calls PublishOpportunity for real today —
// this type exists ready for the first strategy task, proven correct by
// test/kafka/producer_test.go and the real-broker integration test.
type Producer struct {
	client     *kgo.Client
	cfg        Config
	provenance ConfigProvenance
	metrics    *Metrics
	health     *Health
}

// NewProducer constructs a Producer against cfg's brokers, proving
// broker reachability before returning (source task §43's fail-closed
// startup safety applies symmetrically to the producer side, not only
// the consumer). metrics/health may be nil (fresh ones are created).
func NewProducer(ctx context.Context, cfg Config, provenance ConfigProvenance, metrics *Metrics, health *Health) (*Producer, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	client, err := newClient(cfg)
	if err != nil {
		return nil, err
	}
	if metrics == nil {
		metrics = NewMetrics()
	}
	if health == nil {
		health = NewHealth(cfg.Enabled)
	}
	if err := ping(ctx, client, pingTimeout); err != nil {
		client.Close()
		health.MarkError(err, time.Now())
		return nil, fmt.Errorf("kafka: producer startup: %w", err)
	}
	health.SetConnected(true)
	health.SetProducerReady(true)
	return &Producer{client: client, cfg: cfg, provenance: provenance, metrics: metrics, health: health}, nil
}

// PublishOpportunity encodes candidate as analysis.opportunity.v1 and
// publishes it, keyed by symbol (source task §14), synchronously.
func (p *Producer) PublishOpportunity(ctx context.Context, eventID, correlationID, causationID string, candidate opportunity.Candidate, algo AlgorithmVersion, occurredAt time.Time) error {
	payload := OpportunityPayloadFromCandidate(candidate, algo)
	return p.publish(ctx, p.cfg.Topics.AnalysisOpportunity, candidate.Symbol, eventID, correlationID, causationID, payload, occurredAt)
}

// PublishOpportunityInvalidated encodes payload as
// analysis.opportunity.invalidated.v1 and publishes it, keyed by symbol.
func (p *Producer) PublishOpportunityInvalidated(ctx context.Context, eventID, correlationID, causationID string, symbol market.Symbol, payload OpportunityInvalidatedPayload, occurredAt time.Time) error {
	return p.publish(ctx, p.cfg.Topics.AnalysisOpportunityInvalidated, symbol, eventID, correlationID, causationID, payload, occurredAt)
}

func (p *Producer) publish(ctx context.Context, topic string, symbol market.Symbol, eventID, correlationID, causationID string, payload any, occurredAt time.Time) error {
	payloadBytes, err := Encode(payload)
	if err != nil {
		return fmt.Errorf("kafka: encoding %s payload: %w", topic, err)
	}
	env := Envelope{
		EventID:           eventID,
		EventType:         topic,
		EventVersion:      1,
		OccurredAt:        occurredAt.Unix(),
		ProducedAt:        time.Now().Unix(),
		Producer:          p.cfg.ClientID,
		CorrelationID:     correlationID,
		CausationID:       causationID,
		ConfigVersion:     p.provenance.Version,
		ConfigFingerprint: p.provenance.Fingerprint,
		Payload:           payloadBytes,
	}
	if err := env.Validate(); err != nil {
		return err
	}
	envBytes, err := Encode(env)
	if err != nil {
		return fmt.Errorf("kafka: encoding envelope: %w", err)
	}
	record := &kgo.Record{
		Topic:   topic,
		Key:     RecordKey(symbol),
		Value:   envBytes,
		Headers: BuildHeaders(env),
	}

	// ProduceSync (not the async/callback Produce) — blocks until the
	// broker acknowledges or ctx is cancelled, and returns delivery
	// errors directly: source task §21's "no fire-and-forget API where
	// errors disappear," and the caller can tell apart "accepted",
	// "failed before broker", "broker delivery failed", and "context
	// cancelled" from the returned error and ctx.Err().
	done := p.metrics.Time(PhaseProduceDuration, topic, env.EventType)
	results := p.client.ProduceSync(ctx, record)
	done()

	if err := results.FirstErr(); err != nil {
		p.metrics.Count(CounterProduceError, topic, env.EventType, 1)
		p.health.MarkError(err, time.Now())
		return fmt.Errorf("kafka: produce to %s failed: %w", topic, err)
	}
	p.metrics.Count(CounterProduceTotal, topic, env.EventType, 1)
	p.health.MarkProduced(time.Now())
	return nil
}

// Close flushes any in-flight produce within ctx's deadline, then closes
// the underlying client (source task §37).
func (p *Producer) Close(ctx context.Context) error {
	if err := p.client.Flush(ctx); err != nil {
		return fmt.Errorf("kafka: flushing producer: %w", err)
	}
	p.client.Close()
	p.health.SetProducerReady(false)
	return nil
}
