package kafka

import (
	"context"
	"fmt"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

// newClient builds a kgo.Client with cfg's brokers/client ID plus any
// caller-specific options. The current producer needs none; keeping this
// construction in one place prevents broker/client-ID wiring from drifting.
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
// timeout. It is used only for producer initialization and does not create a
// topic or otherwise mutate broker state.
func ping(ctx context.Context, client *kgo.Client, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	if err := client.Ping(ctx); err != nil {
		return fmt.Errorf("kafka: broker unreachable: %w", err)
	}
	return nil
}
