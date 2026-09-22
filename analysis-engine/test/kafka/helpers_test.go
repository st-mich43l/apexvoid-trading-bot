// Shared helpers for the real-broker tests in this package
// (producer_test.go, consumer_test.go, shutdown_test.go) — source task
// §51: "use a real Kafka-protocol-compatible test broker where
// practical... do not make every normal unit test require Docker."
// Every test that calls testBrokers(t) skips cleanly when
// KAFKA_TEST_BROKERS is unset, so `go test ./...` never needs Docker;
// docs/transport/kafka.md documents the exact command to run these for
// real against an ephemeral Redpanda broker.
package kafka_test

import (
	"context"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func testBrokers(t *testing.T) []string {
	t.Helper()
	raw := os.Getenv("KAFKA_TEST_BROKERS")
	if raw == "" {
		t.Skip("KAFKA_TEST_BROKERS not set — skipping real-broker test; see docs/transport/kafka.md for how to run these against a real Redpanda broker")
	}
	return strings.Split(raw, ",")
}

// uniqueTopic gives each test its own topic/group name (isolated and
// explicit auto-creation, source task §44) so concurrent/repeated test
// runs never collide.
func uniqueTopic(base string) string {
	return base + "-test-" + strconv.FormatInt(time.Now().UnixNano(), 36)
}

func testConfig(t *testing.T, base string) kafka.Config {
	cfg := kafka.Config{
		Enabled: true, Brokers: testBrokers(t), ClientID: "apexvoid-analysis-engine-test",
		Topics: kafka.Topics{
			MarketBarClosed:                base + "-bar",
			MarketTick:                     base + "-tick",
			AnalysisOpportunity:            base + "-opportunity",
			AnalysisOpportunityInvalidated: base + "-opportunity-invalidated",
		},
		ConsumerGroup:          uniqueTopic("group"),
		TickConsumptionEnabled: true,
	}
	// Explicit topic creation (source task §44's preferred model: never
	// depend on broker auto-creation for anything meant to resemble
	// production; "for development tests, auto-creation may be
	// acceptable if isolated and explicit" — this IS that explicit
	// creation, isolated per test via uniqueTopic's own random suffix).
	// The real broker this session tests against (Redpanda) does not
	// auto-create topics by default, which is what surfaced the need
	// for this helper in the first place.
	ensureTopics(t, cfg.Brokers,
		cfg.Topics.MarketBarClosed, cfg.Topics.MarketTick,
		cfg.Topics.AnalysisOpportunity, cfg.Topics.AnalysisOpportunityInvalidated,
	)
	return cfg
}

func ensureTopics(t *testing.T, brokers []string, topics ...string) {
	t.Helper()
	client, err := kgo.NewClient(kgo.SeedBrokers(brokers...))
	if err != nil {
		t.Fatalf("ensureTopics: kgo.NewClient: %v", err)
	}
	defer client.Close()
	admin := kadm.NewClient(client)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if _, err := admin.CreateTopics(ctx, 1, 1, nil, topics...); err != nil {
		t.Fatalf("ensureTopics: CreateTopics(%v): %v", topics, err)
	}
}

func testCandidate() opportunity.Candidate {
	return opportunity.Candidate{
		ID: "cand-1", Strategy: "breakoutretest", Symbol: "XAU", Direction: market.Buy,
		Entry:        opportunity.EntryZone{Low: 2000, High: 2002},
		Invalidation: market.PriceLevel{Price: 1990, Label: "structural_low"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 2020}}},
		Evidence:     []opportunity.Evidence{{Code: "m5_bos_up"}},
		Quality:      opportunity.StrategyQuality{Overall: 0.8, Components: map[string]float64{"breakout_quality": 0.9}},
		CreatedAt:    1000, ExpiresAt: 2000,
	}
}

func testBarPayload(symbol string, openTime int64) kafka.BarClosedPayload {
	return kafka.BarClosedPayload{
		CanonicalSymbol: symbol, Timeframe: "M5", OpenTime: openTime, CloseTime: openTime + 300,
		Open: 2000, High: 2005, Low: 1998, Close: 2003, Volume: 10,
	}
}

func barClosedEnvelope(t *testing.T, topic string, p kafka.BarClosedPayload) kafka.Envelope {
	t.Helper()
	payloadBytes, err := kafka.Encode(p)
	if err != nil {
		t.Fatalf("encoding bar payload: %v", err)
	}
	return kafka.Envelope{
		EventID: kafka.NewEventID(), EventType: topic, EventVersion: 1,
		OccurredAt: p.CloseTime, ProducedAt: p.CloseTime, Producer: "test",
		CorrelationID: "corr-test", Payload: payloadBytes,
	}
}

// publishRaw produces a record directly via a fresh kgo client,
// bypassing Producer (which only knows how to publish the opportunity
// event types) — used to seed market.bar.closed.v1 / market.tick.v1
// style records for the consumer-side tests.
func publishRaw(t *testing.T, brokers []string, topic, key string, env kafka.Envelope) {
	t.Helper()
	b, err := kafka.Encode(env)
	if err != nil {
		t.Fatalf("encoding envelope: %v", err)
	}
	publishRawBytes(t, brokers, topic, key, b)
}

// publishRawBytes is publishRaw's lower-level form — takes the record
// value directly, used to seed a deliberately-malformed (not even valid
// JSON) record for the poison-message tests.
func publishRawBytes(t *testing.T, brokers []string, topic, key string, value []byte) {
	t.Helper()
	client, err := kgo.NewClient(kgo.SeedBrokers(brokers...))
	if err != nil {
		t.Fatalf("kgo.NewClient: %v", err)
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	results := client.ProduceSync(ctx, &kgo.Record{Topic: topic, Key: []byte(key), Value: value})
	if err := results.FirstErr(); err != nil {
		t.Fatalf("publishRawBytes: produce failed: %v", err)
	}
}

// testHandler is a kafka.Handler test double — never used in production
// code (internal/engine.KafkaHandler is the real implementation).
type testHandler struct {
	mu           sync.Mutex
	barsHandled  []marketdata.BarEvent
	ticksHandled []kafka.TickPayload

	// failFirstN calls fail with failErr(); calls after that succeed.
	failFirstN int
	failErr    func() error
	calls      int
}

func (h *testHandler) HandleBarClosed(ctx context.Context, event marketdata.BarEvent) error {
	h.mu.Lock()
	h.calls++
	n := h.calls
	h.mu.Unlock()
	if n <= h.failFirstN {
		return h.failErr()
	}
	h.mu.Lock()
	h.barsHandled = append(h.barsHandled, event)
	h.mu.Unlock()
	return nil
}

func (h *testHandler) HandleTick(ctx context.Context, tick kafka.TickPayload) error {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.ticksHandled = append(h.ticksHandled, tick)
	return nil
}

func (h *testHandler) barCount() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.barsHandled)
}
