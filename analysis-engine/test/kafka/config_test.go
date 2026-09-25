package kafka_test

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func repoConfigPath(parts ...string) string {
	return filepath.Join(append([]string{"..", "..", "..", "config"}, parts...)...)
}

func TestKafkaConfigFromConfig_UsesOnlyBusinessEventTopics(t *testing.T) {
	doc, err := config.ResolveDocument(repoConfigPath("apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := engine.KafkaConfigFromConfig(doc)
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.Enabled || cfg.ClientID != "apexvoid-analysis-engine" {
		t.Fatalf("unexpected kafka config: %+v", cfg)
	}
	if cfg.Topics.AnalysisOpportunity != "analysis.opportunity.v1" || cfg.Topics.AnalysisOpportunityInvalidated != "analysis.opportunity.invalidated.v1" {
		t.Fatalf("unexpected analysis topics: %+v", cfg.Topics)
	}
	if _, ok := cfg.TopicSpecs["market.bar.closed.v1"]; ok {
		t.Fatal("market bar Kafka topic must not be active")
	}
	if _, ok := cfg.TopicSpecs["market.tick.v1"]; ok {
		t.Fatal("market tick Kafka topic must not be active")
	}
	for _, topic := range []string{"execution.trade-plan.v1", "execution.trade-event.v1"} {
		if _, ok := cfg.TopicSpecs[topic]; !ok {
			t.Errorf("expected deployment topic spec for %s", topic)
		}
	}
}

func validKafkaConfig() kafka.Config {
	return kafka.Config{Enabled: true, Brokers: []string{"kafka:9092"}, ClientID: "apexvoid-analysis-engine", OutboxPath: "/tmp/apexvoid-test-outbox.json",
		Topics: kafka.Topics{AnalysisOpportunity: "analysis.opportunity.v1", AnalysisOpportunityInvalidated: "analysis.opportunity.invalidated.v1"}}
}

func TestKafkaConfigValidationIsProducerOnly(t *testing.T) {
	if err := validKafkaConfig().Validate(); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*kafka.Config){
		func(c *kafka.Config) { c.Brokers = nil },
		func(c *kafka.Config) { c.ClientID = "" },
		func(c *kafka.Config) { c.Topics.AnalysisOpportunity = "" },
		func(c *kafka.Config) { c.Topics.AnalysisOpportunityInvalidated = c.Topics.AnalysisOpportunity },
	} {
		cfg := validKafkaConfig()
		mutate(&cfg)
		if err := cfg.Validate(); err == nil {
			t.Error("expected invalid producer config to fail")
		}
	}
}
