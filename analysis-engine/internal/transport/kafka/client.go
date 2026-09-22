package kafka

import (
	"context"
	"fmt"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

// newClient builds a kgo.Client with cfg's brokers/client ID plus any
// caller-specific options (consumer-group options for Consumer, none
// extra for Producer). Centralized here so Producer and Consumer never
// duplicate broker/client-id wiring (source task §7: avoid one giant
// kafka.go, but also avoid needless duplication across the split files).
//
// Producer durability is left at franz-go's own defaults deliberately
// (ADR-008: idempotent producer, acks=all) — source task §21's "do not
// lower durability for benchmark speed" is honored by never overriding
// these downward, not by re-specifying them here.
func newClient(cfg Config, opts ...kgo.Opt) (*kgo.Client, error) {
	base := []kgo.Opt{
		kgo.SeedBrokers(cfg.Brokers...),
		kgo.ClientID(cfg.ClientID),
	}
	client, err := kgo.NewClient(append(base, opts...)...)
	if err != nil {
		return nil, fmt.Errorf("kafka: constructing client: %w", err)
	}
	return client, nil
}

// ping proves at least one configured broker is reachable within
// timeout — source task §43's startup-safety fail-closed check
// ("consumer startup should fail closed if Kafka is enabled but... no
// broker reachable within startup policy"). Does not create a topic or
// otherwise mutate broker state.
func ping(ctx context.Context, client *kgo.Client, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	if err := client.Ping(ctx); err != nil {
		return fmt.Errorf("kafka: broker unreachable: %w", err)
	}
	return nil
}
