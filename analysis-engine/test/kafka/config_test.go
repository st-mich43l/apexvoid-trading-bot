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

// TestKafkaConfigFromConfig_ResolvesTheRealCheckedInConfig proves
// engine.KafkaConfigFromConfig reads the ACTUAL config/apexvoid.yml
// (via config/transport.yml), not a copied test fixture — the same
// discipline test/config's own tests already use (source task §58).
func TestKafkaConfigFromConfig_ResolvesTheRealCheckedInConfig(t *testing.T) {
	doc, err := config.ResolveDocument(repoConfigPath("apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}
	cfg, err := engine.KafkaConfigFromConfig(doc)
	if err != nil {
		t.Fatalf("KafkaConfigFromConfig: %v", err)
	}
	// Flipped true by the cTrader -> Kafka -> Analysis Engine pipeline
	// task: a real broker now exists in deployment-template/docker-compose.yml.j2
	// and docker-compose.yml, provisioned by kafka-init before any
	// producer/consumer starts. See docs/transport/kafka.md's "Kafka
	// enablement" section — this was `false` through the prior
	// Go-transport-only task specifically because no broker existed yet.
	if !cfg.Enabled {
		t.Error("expected the checked-in production config to have transport.kafka.enabled=true now that a real broker is part of the deployment topology")
	}
	if cfg.Topics.MarketBarClosed != "market.bar.closed.v1" {
		t.Errorf("expected topics.market_bar_closed=market.bar.closed.v1, got %q", cfg.Topics.MarketBarClosed)
	}
	if cfg.Topics.MarketTick != "market.tick.v1" {
		t.Errorf("expected topics.market_tick=market.tick.v1, got %q", cfg.Topics.MarketTick)
	}
	if cfg.Topics.AnalysisOpportunity != "analysis.opportunity.v1" {
		t.Errorf("expected topics.analysis_opportunity=analysis.opportunity.v1, got %q", cfg.Topics.AnalysisOpportunity)
	}
	if cfg.Topics.AnalysisOpportunityInvalidated != "analysis.opportunity.invalidated.v1" {
		t.Errorf("expected topics.analysis_opportunity_invalidated=analysis.opportunity.invalidated.v1, got %q", cfg.Topics.AnalysisOpportunityInvalidated)
	}
	if cfg.ConsumerGroup != "apexvoid-analysis-engine-v1" {
		t.Errorf("expected consumer_groups.analysis_engine=apexvoid-analysis-engine-v1, got %q", cfg.ConsumerGroup)
	}
	if cfg.ClientID != "apexvoid-analysis-engine" {
		t.Errorf("expected client_id.analysis_engine=apexvoid-analysis-engine, got %q", cfg.ClientID)
	}
	if cfg.TickConsumptionEnabled {
		t.Error("expected tick_consumption_enabled=false by default (source task §3)")
	}
	if len(cfg.Brokers) == 0 {
		t.Error("expected at least one broker entry even while disabled (the shape is always present)")
	}
}

func TestConfigProvenanceFromConfig_ProducesAStableNonEmptyFingerprint(t *testing.T) {
	doc, err := config.ResolveDocument(repoConfigPath("apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}
	provenance, err := engine.ConfigProvenanceFromConfig(doc)
	if err != nil {
		t.Fatalf("ConfigProvenanceFromConfig: %v", err)
	}
	if provenance.Version != 3 {
		t.Errorf("expected Configuration V3's own version marker (3), got %d", provenance.Version)
	}
	if provenance.Fingerprint == "" {
		t.Error("expected a non-empty fingerprint")
	}

	// Resolving the identical config twice must produce the identical
	// fingerprint — it exists to answer "which exact configuration
	// generated this opportunity?" historically (source task §13), which
	// only works if it's deterministic.
	doc2, err := config.ResolveDocument(repoConfigPath("apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument (second call): %v", err)
	}
	provenance2, err := engine.ConfigProvenanceFromConfig(doc2)
	if err != nil {
		t.Fatalf("ConfigProvenanceFromConfig (second call): %v", err)
	}
	if provenance.Fingerprint != provenance2.Fingerprint {
		t.Errorf("expected a deterministic fingerprint across identical resolutions, got %q vs %q", provenance.Fingerprint, provenance2.Fingerprint)
	}
}

// --- Config.Validate fail-closed cases (source task §5) ---

func validKafkaConfig() kafka.Config {
	return kafka.Config{
		Enabled:  true,
		Brokers:  []string{"kafka:9092"},
		ClientID: "apexvoid-analysis-engine",
		Topics: kafka.Topics{
			MarketBarClosed: "market.bar.closed.v1", MarketTick: "market.tick.v1",
			AnalysisOpportunity: "analysis.opportunity.v1", AnalysisOpportunityInvalidated: "analysis.opportunity.invalidated.v1",
		},
		ConsumerGroup: "apexvoid-analysis-engine-v1",
	}
}

func TestKafkaConfig_Validate_AcceptsAWellFormedConfig(t *testing.T) {
	if err := validKafkaConfig().Validate(); err != nil {
		t.Fatalf("expected a valid config to pass, got: %v", err)
	}
}

func TestKafkaConfig_Validate_DisabledConfigNeverValidatesFurther(t *testing.T) {
	cfg := kafka.Config{Enabled: false} // every other field left zero-valued/empty
	if err := cfg.Validate(); err != nil {
		t.Fatalf("expected a disabled config to always pass validation regardless of its other fields, got: %v", err)
	}
}

func TestKafkaConfig_Validate_RejectsNoBrokers(t *testing.T) {
	cfg := validKafkaConfig()
	cfg.Brokers = nil
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected an error for zero brokers")
	}
}

func TestKafkaConfig_Validate_RejectsAnEmptyBrokerAddress(t *testing.T) {
	cfg := validKafkaConfig()
	cfg.Brokers = []string{"kafka:9092", "  "}
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected an error for an empty broker address")
	}
}

func TestKafkaConfig_Validate_RejectsAMissingTopic(t *testing.T) {
	cfg := validKafkaConfig()
	cfg.Topics.MarketTick = ""
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected an error for a missing required topic")
	}
}

func TestKafkaConfig_Validate_RejectsAMissingConsumerGroup(t *testing.T) {
	cfg := validKafkaConfig()
	cfg.ConsumerGroup = ""
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected an error for a missing analysis-engine consumer group")
	}
}

func TestKafkaConfig_Validate_RejectsDuplicateLogicalTopics(t *testing.T) {
	cfg := validKafkaConfig()
	cfg.Topics.MarketTick = cfg.Topics.MarketBarClosed // two logical topics pointed at the same name
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected an error for duplicate logical topics")
	}
}

func TestKafkaConfig_Validate_RejectsAnInvalidClientID(t *testing.T) {
	cases := []string{"", "   ", "has a space", "has/slash"}
	for _, id := range cases {
		cfg := validKafkaConfig()
		cfg.ClientID = id
		if err := cfg.Validate(); err == nil {
			t.Errorf("expected client_id %q to be rejected", id)
		}
	}
}
