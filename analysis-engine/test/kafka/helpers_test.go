// Real-broker helpers for the producer-only Kafka suite. Tests skip cleanly
// without KAFKA_TEST_BROKERS, so ordinary unit tests never require Docker.
package kafka_test

import (
	"context"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func testBrokers(t *testing.T) []string {
	t.Helper()
	raw := os.Getenv("KAFKA_TEST_BROKERS")
	if raw == "" {
		t.Skip("KAFKA_TEST_BROKERS not set — skipping real Kafka producer test")
	}
	return strings.Split(raw, ",")
}

func uniqueTopic(base string) string {
	return base + "-test-" + strconv.FormatInt(time.Now().UnixNano(), 36)
}

func testConfig(t *testing.T, base string) kafka.Config {
	cfg := kafka.Config{
		Enabled: true, Brokers: testBrokers(t), ClientID: "apexvoid-analysis-engine-test",
		Topics: kafka.Topics{AnalysisOpportunity: base + "-opportunity", AnalysisOpportunityInvalidated: base + "-opportunity-invalidated"},
	}
	ensureTopics(t, cfg.Brokers, cfg.Topics.AnalysisOpportunity, cfg.Topics.AnalysisOpportunityInvalidated)
	return cfg
}

func ensureTopics(t *testing.T, brokers []string, topics ...string) {
	t.Helper()
	client, err := kgo.NewClient(kgo.SeedBrokers(brokers...))
	if err != nil {
		t.Fatalf("kgo.NewClient: %v", err)
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if _, err := kadm.NewClient(client).CreateTopics(ctx, 1, 1, nil, topics...); err != nil {
		t.Fatalf("CreateTopics(%v): %v", topics, err)
	}
}

func testCandidate() opportunity.Candidate {
	return opportunity.Candidate{
		ID: "cand-1", Strategy: "breakoutretest", Symbol: "XAU", Direction: market.Buy,
		Entry: opportunity.EntryZone{Low: 2000, High: 2002}, Invalidation: market.PriceLevel{Price: 1990, Label: "structural_low"},
		Targets: []opportunity.Target{{Price: market.PriceLevel{Price: 2020}}}, Evidence: []opportunity.Evidence{{Code: "m5_bos_up"}},
		Quality: opportunity.StrategyQuality{Overall: 0.8, Components: map[string]float64{"breakout_quality": 0.9}}, CreatedAt: 1000, ExpiresAt: 2000,
	}
}
