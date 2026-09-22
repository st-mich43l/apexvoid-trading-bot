package kafka

import (
	"fmt"
	"strings"
)

// Config is analysis-engine's fully-resolved Kafka transport
// configuration — populated by internal/engine/config.go's
// KafkaConfigFromConfig, the one place internal/config.Document is read
// (see that file's own doc comment: engine is the sole config.Document
// reader in this module; every other package, including this one, takes
// plain Go values). No field here has a hidden default: every value is
// explicit YAML (config/transport.yml's transport.kafka.*) or a
// documented protocol constant elsewhere in this package (source task
// §70 — "no hidden Kafka defaults").
type Config struct {
	Enabled bool

	Brokers  []string
	ClientID string // transport.kafka.client_id.analysis_engine

	Topics        Topics
	TopicSpecs    map[string]TopicSpec
	ConsumerGroup string // transport.kafka.consumer_groups.analysis_engine

	TickConsumptionEnabled bool // transport.kafka.tick_consumption_enabled — source task §3
}

// TopicSpec is the broker-side contract for one configured topic.
type TopicSpec struct {
	Partitions      int32
	Replication     int16
	RetentionMillis int64
}

// Topics names every topic this service reads or writes — never
// hardcoded elsewhere in this package's production code (source task
// §5: "Do NOT hardcode topic names inside Go production code").
type Topics struct {
	MarketBarClosed                string
	MarketTick                     string
	AnalysisOpportunity            string
	AnalysisOpportunityInvalidated string
}

// Validate fails closed per source task §5: no brokers, an empty broker
// address, a missing required topic, a missing consumer group, a
// duplicate logical topic, or an invalid client ID must all be rejected
// BEFORE any client is constructed — never discovered later as a
// confusing runtime connection failure. When Kafka is disabled, nothing
// below is checked: an unconfigured Kafka section is not an error for a
// deployment that isn't using Kafka yet (source task §5's "when
// disabled, the engine must not silently publish/consume through Redis
// as a hidden fallback" implies Kafka-disabled is a legitimate,
// supported mode, not a degraded one).
func (c Config) Validate() error {
	if !c.Enabled {
		return nil
	}
	if len(c.Brokers) == 0 {
		return fmt.Errorf("kafka: transport.kafka.enabled=true requires at least one broker")
	}
	for i, b := range c.Brokers {
		if strings.TrimSpace(b) == "" {
			return fmt.Errorf("kafka: transport.kafka.brokers[%d] is empty", i)
		}
	}
	if err := validateClientID(c.ClientID); err != nil {
		return err
	}
	if strings.TrimSpace(c.ConsumerGroup) == "" {
		return fmt.Errorf("kafka: transport.kafka.consumer_groups.analysis_engine is required")
	}
	topics := map[string]string{
		"market_bar_closed":                c.Topics.MarketBarClosed,
		"market_tick":                      c.Topics.MarketTick,
		"analysis_opportunity":             c.Topics.AnalysisOpportunity,
		"analysis_opportunity_invalidated": c.Topics.AnalysisOpportunityInvalidated,
	}
	seen := make(map[string]string, len(topics))
	for _, name := range []string{"market_bar_closed", "market_tick", "analysis_opportunity", "analysis_opportunity_invalidated"} {
		value := topics[name]
		if strings.TrimSpace(value) == "" {
			return fmt.Errorf("kafka: transport.kafka.topics.%s is required", name)
		}
		if other, dup := seen[value]; dup {
			return fmt.Errorf("kafka: transport.kafka.topics.%s and topics.%s both name %q — every logical topic must be distinct", name, other, value)
		}
		seen[value] = name
	}
	return nil
}

func validateClientID(id string) error {
	trimmed := strings.TrimSpace(id)
	if trimmed == "" {
		return fmt.Errorf("kafka: transport.kafka.client_id.analysis_engine is required")
	}
	for _, r := range trimmed {
		if r <= ' ' || r == '/' || r > '~' {
			return fmt.Errorf("kafka: transport.kafka.client_id.analysis_engine %q contains an invalid character", id)
		}
	}
	return nil
}
