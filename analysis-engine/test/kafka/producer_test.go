package kafka_test

import (
	"context"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// TestProducer_PublishOpportunity_ProducesCorrectTopicKeyEnvelopeAndPayload
// covers source task §56 in one real-broker round trip: correct topic,
// correct key, correct event type, correct envelope, correct
// serialization.
func TestProducer_PublishOpportunity_ProducesCorrectTopicKeyEnvelopeAndPayload(t *testing.T) {
	base := uniqueTopic("producer")
	cfg := testConfig(t, base)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	producer, err := kafka.NewProducer(ctx, cfg, kafka.ConfigProvenance{Version: 3, Fingerprint: "abc123"}, nil, nil)
	if err != nil {
		t.Fatalf("NewProducer: %v", err)
	}
	defer producer.Close(ctx)

	algo := kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"}
	if err := producer.PublishOpportunity(ctx, "corr-1", "bar-event-1", testCandidate(), algo, time.Unix(1000, 0)); err != nil {
		t.Fatalf("PublishOpportunity: %v", err)
	}

	got := consumeOne(t, cfg.Brokers, cfg.Topics.AnalysisOpportunity, ctx)

	if got.Topic != cfg.Topics.AnalysisOpportunity {
		t.Errorf("expected topic %q, got %q", cfg.Topics.AnalysisOpportunity, got.Topic)
	}
	if string(got.Key) != "XAU" {
		t.Errorf("expected record key \"XAU\" (canonical symbol, source task §14), got %q", string(got.Key))
	}

	var env kafka.Envelope
	if err := kafka.DecodeStrict(got.Value, &env); err != nil {
		t.Fatalf("decoding envelope: %v", err)
	}
	if env.EventType != cfg.Topics.AnalysisOpportunity {
		t.Errorf("expected envelope event_type == topic, got %q", env.EventType)
	}
	if env.EventVersion != 1 {
		t.Errorf("expected event_version=1, got %d", env.EventVersion)
	}
	if env.CorrelationID != "corr-1" {
		t.Errorf("expected correlation_id=corr-1, got %q", env.CorrelationID)
	}
	if env.CausationID != "bar-event-1" {
		t.Errorf("expected causation_id=bar-event-1, got %q", env.CausationID)
	}
	if env.ConfigVersion != 3 || env.ConfigFingerprint != "abc123" {
		t.Errorf("expected config provenance carried through, got version=%d fingerprint=%q", env.ConfigVersion, env.ConfigFingerprint)
	}
	if env.EventID == "" {
		t.Error("expected a non-empty event_id")
	}

	var payload kafka.OpportunityPayload
	if err := kafka.DecodeStrict(env.Payload, &payload); err != nil {
		t.Fatalf("decoding payload: %v", err)
	}
	if payload.ID != "cand-1" || payload.Symbol != "XAU" || payload.Direction != "BUY" {
		t.Errorf("unexpected payload: %+v", payload)
	}
	if payload.AlgorithmVersion.Structure != "v2" || payload.AlgorithmVersion.Liquidity != "v1" {
		t.Errorf("expected algorithm_version to be carried through, got %+v", payload.AlgorithmVersion)
	}

	foundEventType, foundContentType := false, false
	for _, h := range got.Headers {
		if h.Key == kafka.HeaderEventType && string(h.Value) == cfg.Topics.AnalysisOpportunity {
			foundEventType = true
		}
		if h.Key == kafka.HeaderContentType && string(h.Value) == kafka.ContentTypeJSON {
			foundContentType = true
		}
	}
	if !foundEventType {
		t.Error("expected an event_type header matching the topic")
	}
	if !foundContentType {
		t.Error("expected a content_type=application/json header")
	}
}

func TestProducer_PublishOpportunityInvalidated_ProducesToItsOwnTopic(t *testing.T) {
	base := uniqueTopic("producer-inv")
	cfg := testConfig(t, base)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	producer, err := kafka.NewProducer(ctx, cfg, kafka.ConfigProvenance{}, nil, nil)
	if err != nil {
		t.Fatalf("NewProducer: %v", err)
	}
	defer producer.Close(ctx)

	payload := kafka.OpportunityInvalidatedPayload{
		OpportunityID: "cand-1", Symbol: "XAU", Strategy: "breakoutretest", ReasonCode: "STRUCTURE_INVALIDATED", InvalidatedAt: 1500,
	}
	if err := producer.PublishOpportunityInvalidated(ctx, "corr-2", "cand-1", "XAU", payload, time.Unix(1500, 0)); err != nil {
		t.Fatalf("PublishOpportunityInvalidated: %v", err)
	}

	got := consumeOne(t, cfg.Brokers, cfg.Topics.AnalysisOpportunityInvalidated, ctx)
	if got.Topic != cfg.Topics.AnalysisOpportunityInvalidated {
		t.Errorf("expected topic %q, got %q", cfg.Topics.AnalysisOpportunityInvalidated, got.Topic)
	}

	var env kafka.Envelope
	if err := kafka.DecodeStrict(got.Value, &env); err != nil {
		t.Fatalf("decoding envelope: %v", err)
	}
	var decoded kafka.OpportunityInvalidatedPayload
	if err := kafka.DecodeStrict(env.Payload, &decoded); err != nil {
		t.Fatalf("decoding payload: %v", err)
	}
	if decoded != payload {
		t.Errorf("round-trip mismatch: got=%+v want=%+v", decoded, payload)
	}
}

func TestProducer_PublishOpportunity_PropagatesContextCancellation(t *testing.T) {
	base := uniqueTopic("producer-cancel")
	cfg := testConfig(t, base)
	setupCtx, cancelSetup := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancelSetup()

	producer, err := kafka.NewProducer(setupCtx, cfg, kafka.ConfigProvenance{}, nil, nil)
	if err != nil {
		t.Fatalf("NewProducer: %v", err)
	}
	defer producer.Close(setupCtx)

	cancelledCtx, cancel := context.WithCancel(context.Background())
	cancel()
	err = producer.PublishOpportunity(cancelledCtx, "corr", "", testCandidate(), kafka.AlgorithmVersion{}, time.Unix(1, 0))
	if err == nil {
		t.Fatal("expected an error when publishing with an already-cancelled context")
	}
}

func TestProducer_PublishOpportunity_PropagatesBrokerDeliveryErrors(t *testing.T) {
	cfg := testConfig(t, uniqueTopic("producer-baddelivery"))
	// Kafka topic names may only contain [a-zA-Z0-9._-]; this forces a
	// real broker-side rejection (auto-create validation failure or
	// unknown-topic), proving delivery errors reach the caller rather
	// than disappearing (source task §21).
	cfg.Topics.AnalysisOpportunity = "invalid topic name with spaces!!"
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	producer, err := kafka.NewProducer(ctx, cfg, kafka.ConfigProvenance{}, nil, nil)
	if err != nil {
		t.Fatalf("NewProducer: %v", err)
	}
	defer producer.Close(ctx)

	err = producer.PublishOpportunity(ctx, "corr", "", testCandidate(), kafka.AlgorithmVersion{}, time.Unix(1, 0))
	if err == nil {
		t.Fatal("expected a delivery error for an invalid topic name, got nil")
	}
}

func TestNewProducer_FailsClosedWhenNoBrokerIsReachable(t *testing.T) {
	testBrokers(t) // still require KAFKA_TEST_BROKERS to be set — this test proves a DIFFERENT (unreachable) address fails, not that Kafka is entirely absent from the environment
	cfg := testConfig(t, uniqueTopic("producer-unreachable"))
	cfg.Brokers = []string{"127.0.0.1:1"} // a port nothing listens on
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := kafka.NewProducer(ctx, cfg, kafka.ConfigProvenance{}, nil, nil)
	if err == nil {
		t.Fatal("expected NewProducer to fail closed against an unreachable broker (source task §43)")
	}
}

// consumeOne polls until exactly one record is available on topic,
// failing the test if none arrives within ctx's deadline.
func consumeOne(t *testing.T, brokers []string, topic string, ctx context.Context) *kgo.Record {
	t.Helper()
	client, err := kgo.NewClient(
		kgo.SeedBrokers(brokers...),
		kgo.ConsumeTopics(topic),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()),
	)
	if err != nil {
		t.Fatalf("kgo.NewClient: %v", err)
	}
	defer client.Close()

	for {
		if ctx.Err() != nil {
			t.Fatalf("timed out waiting for a record on %s: %v", topic, ctx.Err())
		}
		fetches := client.PollFetches(ctx)
		fetches.EachError(func(topic string, partition int32, err error) {
			t.Errorf("fetch error: topic=%s partition=%d err=%v", topic, partition, err)
		})
		var got *kgo.Record
		fetches.EachRecord(func(r *kgo.Record) {
			if got == nil {
				got = r
			}
		})
		if got != nil {
			return got
		}
	}
}
