// Command kafka-admin provisions the explicitly configured ApexVoid Kafka
// topics and verifies their broker-side contract.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func main() {
	path := os.Getenv(config.RootFileEnv)
	if path == "" {
		fmt.Fprintf(os.Stderr, "kafka-admin: %s not set\n", config.RootFileEnv)
		os.Exit(1)
	}
	doc, err := config.ResolveDocument(path)
	if err != nil {
		fatal("load configuration", err)
	}
	cfg, err := engine.KafkaConfigFromConfig(doc)
	if err != nil {
		fatal("load Kafka configuration", err)
	}
	if !cfg.Enabled {
		fatal("provision topics", fmt.Errorf("transport.kafka.enabled=false"))
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := kafka.ProvisionTopics(ctx, cfg); err != nil {
		fatal("provision topics", err)
	}
	for topic, spec := range cfg.TopicSpecs {
		fmt.Fprintf(os.Stdout, "kafka-admin: verified topic=%s partitions=%d replication=%d retention_ms=%d\n", topic, spec.Partitions, spec.Replication, spec.RetentionMillis)
	}
}

func fatal(action string, err error) {
	fmt.Fprintf(os.Stderr, "kafka-admin: %s: %v\n", action, err)
	os.Exit(1)
}
