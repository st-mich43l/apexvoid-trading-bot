package kafka

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kerr"
)

// ProvisionTopics creates the configured topics when absent, then verifies
// their partition, replication, and retention contract. Existing topics are
// never altered silently: a mismatch fails the deployment.
func ProvisionTopics(ctx context.Context, cfg Config) error {
	if err := cfg.Validate(); err != nil {
		return err
	}
	if len(cfg.TopicSpecs) == 0 {
		return fmt.Errorf("kafka: no topic_specs configured")
	}
	client, err := newClient(cfg)
	if err != nil {
		return err
	}
	defer client.Close()
	if err := ping(ctx, client, pingTimeout); err != nil {
		return err
	}
	admin := kadm.NewClient(client)

	names := make([]string, 0, len(cfg.TopicSpecs))
	for name := range cfg.TopicSpecs {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		spec := cfg.TopicSpecs[name]
		if spec.Partitions <= 0 || spec.Replication <= 0 || spec.RetentionMillis <= 0 {
			return fmt.Errorf("kafka: invalid topic spec %q: %+v", name, spec)
		}
		response, err := admin.CreateTopic(ctx, spec.Partitions, spec.Replication,
			map[string]*string{"retention.ms": kadm.StringPtr(strconv.FormatInt(spec.RetentionMillis, 10))}, name)
		if err != nil && !errors.Is(err, kerr.TopicAlreadyExists) {
			return fmt.Errorf("kafka: create topic %s: %w", name, err)
		}
		if response.Err != nil && !errors.Is(response.Err, kerr.TopicAlreadyExists) {
			return fmt.Errorf("kafka: create topic %s: %w", name, response.Err)
		}
	}

	var metadata kadm.Metadata
	for attempt := 0; attempt < 20; attempt++ {
		metadata, err = admin.Metadata(ctx, names...)
		if err == nil {
			err = metadata.Topics.Error()
		}
		if err == nil {
			break
		}
		if !errors.Is(err, kerr.UnknownTopicOrPartition) && !errors.Is(err, kerr.LeaderNotAvailable) {
			return fmt.Errorf("kafka: verify topic metadata: %w", err)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(250 * time.Millisecond):
		}
	}
	if err != nil {
		return fmt.Errorf("kafka: verify topic metadata: %w", err)
	}
	for _, name := range names {
		detail, ok := metadata.Topics[name]
		if !ok {
			return fmt.Errorf("kafka: topic %s missing from metadata", name)
		}
		spec := cfg.TopicSpecs[name]
		if got := int32(len(detail.Partitions)); got != spec.Partitions {
			return fmt.Errorf("kafka: topic %s has %d partitions, configured %d", name, got, spec.Partitions)
		}
		if got := detail.Partitions.NumReplicas(); got != int(spec.Replication) {
			return fmt.Errorf("kafka: topic %s has %d replicas, configured %d", name, got, spec.Replication)
		}
	}

	configs, err := admin.DescribeTopicConfigs(ctx, names...)
	if err != nil {
		return fmt.Errorf("kafka: verify topic configs: %w", err)
	}
	for _, name := range names {
		resource, err := configs.On(name, nil)
		if err != nil {
			return fmt.Errorf("kafka: verify config for %s: %w", name, err)
		}
		want := strconv.FormatInt(cfg.TopicSpecs[name].RetentionMillis, 10)
		found := false
		for _, setting := range resource.Configs {
			if setting.Key == "retention.ms" {
				found = true
				if setting.MaybeValue() != want {
					return fmt.Errorf("kafka: topic %s retention.ms=%q, configured %q", name, setting.MaybeValue(), want)
				}
			}
		}
		if !found {
			return fmt.Errorf("kafka: topic %s does not expose retention.ms", name)
		}
	}
	return nil
}
