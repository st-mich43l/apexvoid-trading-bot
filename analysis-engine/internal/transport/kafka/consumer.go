package kafka

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

// defaultHandlerRetries/defaultHandlerBackoff bound the APPLICATION
// handler retry (source task §20) — distinct from franz-go's own
// broker/network-level retry (ADR-008), which this package never
// re-implements. Documented protocol constants, not runtime-tunable
// settings (source task §70): the exact retry count/backoff changes
// nothing about correctness, only how long a genuinely-transient blip
// takes to resolve.
const (
	defaultHandlerRetries = 3
	defaultHandlerBackoff = 200 * time.Millisecond
)

// Consumer consumes market.bar.closed.v1 (and, when enabled,
// market.tick.v1), decoding/validating/adapting each record before
// handing it to Handler — source task §25's pipeline: Kafka record ->
// envelope decoder -> contract validation -> typed market event ->
// engine.
type Consumer struct {
	client  *kgo.Client
	cfg     Config
	handler Handler
	metrics *Metrics
	health  *Health

	handlerRetries int
	handlerBackoff time.Duration
}

// NewConsumer constructs a consumer for cfg's configured topics
// (market_bar_closed always; market_tick only when
// cfg.TickConsumptionEnabled, source task §3), proving broker
// reachability before returning (source task §43: fail closed at
// startup, never silently run disconnected while advertising ready).
func NewConsumer(ctx context.Context, cfg Config, handler Handler, metrics *Metrics, health *Health) (*Consumer, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if handler == nil {
		return nil, fmt.Errorf("kafka: NewConsumer requires a non-nil Handler")
	}
	topics := []string{cfg.Topics.MarketBarClosed}
	if cfg.TickConsumptionEnabled {
		topics = append(topics, cfg.Topics.MarketTick)
	}
	client, err := newClient(cfg,
		kgo.ConsumerGroup(cfg.ConsumerGroup),
		kgo.ConsumeTopics(topics...),
		kgo.DisableAutoCommit(),    // source task §18: never commit before the engine has accepted the record
		kgo.BlockRebalanceOnPoll(), // source task §46: never process/commit mid-rebalance
	)
	if err != nil {
		return nil, err
	}
	if metrics == nil {
		metrics = NewMetrics()
	}
	if health == nil {
		health = NewHealth(cfg.Enabled)
	}
	if err := ping(ctx, client, pingTimeout); err != nil {
		client.Close()
		health.MarkError(err, time.Now())
		return nil, fmt.Errorf("kafka: consumer startup: %w", err)
	}
	health.SetConnected(true)
	return &Consumer{
		client: client, cfg: cfg, handler: handler, metrics: metrics, health: health,
		handlerRetries: defaultHandlerRetries, handlerBackoff: defaultHandlerBackoff,
	}, nil
}

// Run polls and processes records until ctx is cancelled — source task
// §37's shutdown model: context cancellation is the only shutdown
// signal; no new record is dispatched to Handler after it fires.
// Returns nil on a clean shutdown. Returns a non-nil error only when a
// handler keeps failing TRANSIENTLY past its bounded retry budget — that
// offset is never committed, so the record is redelivered after restart
// (source task §18/§23's at-least-once + application-idempotency
// design, ADR-009) — the caller/operator needs to know processing has
// genuinely stalled, not have it silently retried forever inside this
// call.
func (c *Consumer) Run(ctx context.Context) error {
	c.health.SetConsumerRunning(true)
	defer c.health.SetConsumerRunning(false)
	defer c.client.Close()

	for {
		if ctx.Err() != nil {
			return nil
		}
		fetches := c.client.PollFetches(ctx)
		if fetches.IsClientClosed() {
			return nil
		}
		fetches.EachError(func(topic string, partition int32, err error) {
			c.metrics.Count(CounterConsumeError, topic, "", 1)
			c.health.MarkError(err, time.Now())
			slog.Error("kafka: fetch error", "topic", topic, "partition", partition, "error", err)
		})

		var stalled error
		fetches.EachRecord(func(record *kgo.Record) {
			// A prior record in this batch stalled on a genuinely
			// transient failure, or shutdown began mid-batch — stop
			// dispatching further records; this partition's progress
			// resumes from the same point on the next Run (source task
			// §18: never skip past unacknowledged work).
			if stalled != nil || ctx.Err() != nil {
				return
			}
			if err := c.processOne(ctx, record); err != nil {
				stalled = err
			}
		})
		c.client.AllowRebalance() // must always run, even when stalled — never leave a rebalance permanently blocked
		if stalled != nil {
			return stalled
		}
	}
}

func (c *Consumer) processOne(ctx context.Context, record *kgo.Record) error {
	topic := record.Topic
	c.metrics.Count(CounterConsumeTotal, topic, "", 1)
	c.metrics.Record(PhaseConsumerLag, topic, "", time.Since(record.Timestamp))

	var env Envelope
	if err := DecodeStrict(record.Value, &env); err != nil {
		c.metrics.Count(CounterDecodeError, topic, "", 1)
		logPermanentFailure(record, Envelope{}, "envelope decode failed", err)
		return c.commit(ctx, record)
	}
	if err := env.Validate(); err != nil {
		c.metrics.Count(CounterDecodeError, topic, env.EventType, 1)
		logPermanentFailure(record, env, "envelope validation failed", err)
		return c.commit(ctx, record)
	}
	c.health.MarkConsumed(time.Now())

	done := c.metrics.Time(PhaseHandlerDuration, topic, env.EventType)
	handlerErr := c.dispatch(ctx, topic, env, record)
	done()
	if handlerErr == nil {
		return c.commit(ctx, record)
	}
	if IsPermanent(handlerErr) {
		c.metrics.Count(CounterHandlerError, topic, env.EventType, 1)
		logPermanentFailure(record, env, "handler permanently failed", handlerErr)
		return c.commit(ctx, record) // §19: never retry a permanent contract failure forever
	}

	// Transient: bounded local retry with backoff (§20) — never an
	// infinite TIGHT loop. If still failing after the bound, this
	// record's offset is deliberately left uncommitted and Run returns
	// an error: silently skipping real, still-failing market data would
	// be worse than visibly stalling this partition until an operator
	// (or a restart, if the condition was transient after all) resolves
	// it.
	for attempt := 1; attempt < c.handlerRetries; attempt++ {
		select {
		case <-ctx.Done():
			return nil // shutdown interrupts the retry wait (§20/§37) — record stays uncommitted, Run's outer loop exits on the next ctx check
		case <-time.After(c.handlerBackoff * time.Duration(attempt)):
		}
		done := c.metrics.Time(PhaseHandlerDuration, topic, env.EventType)
		handlerErr = c.dispatch(ctx, topic, env, record)
		done()
		if handlerErr == nil {
			return c.commit(ctx, record)
		}
		if IsPermanent(handlerErr) {
			c.metrics.Count(CounterHandlerError, topic, env.EventType, 1)
			logPermanentFailure(record, env, "handler permanently failed on retry", handlerErr)
			return c.commit(ctx, record)
		}
	}
	c.metrics.Count(CounterHandlerError, topic, env.EventType, 1)
	c.health.MarkError(handlerErr, time.Now())
	return fmt.Errorf("kafka: %s partition=%d offset=%d: handler still failing transiently after %d attempts: %w",
		topic, record.Partition, record.Offset, c.handlerRetries, handlerErr)
}

func (c *Consumer) dispatch(ctx context.Context, topic string, env Envelope, record *kgo.Record) error {
	switch topic {
	case c.cfg.Topics.MarketBarClosed:
		var payload BarClosedPayload
		if err := DecodeStrict(env.Payload, &payload); err != nil {
			return Permanent(fmt.Errorf("kafka: decoding %s payload: %w", topic, err))
		}
		event, err := BarEventFromPayload(payload)
		if err != nil {
			return Permanent(err)
		}
		return c.handler.HandleBarClosed(ctx, event)
	case c.cfg.Topics.MarketTick:
		var payload TickPayload
		if err := DecodeStrict(env.Payload, &payload); err != nil {
			return Permanent(fmt.Errorf("kafka: decoding %s payload: %w", topic, err))
		}
		if err := payload.Validate(); err != nil {
			return Permanent(err)
		}
		return c.handler.HandleTick(ctx, payload)
	default:
		_ = record
		return Permanent(fmt.Errorf("kafka: record on unrecognized topic %q", topic))
	}
}

func (c *Consumer) commit(ctx context.Context, record *kgo.Record) error {
	if err := c.client.CommitRecords(ctx, record); err != nil {
		return fmt.Errorf("kafka: committing offset for %s partition=%d offset=%d: %w", record.Topic, record.Partition, record.Offset, err)
	}
	return nil
}

// logPermanentFailure preserves topic/partition/offset/event metadata on
// a permanent (poison-message) failure (source task §19/§39) — never the
// full high-frequency payload by default, and never a secret.
func logPermanentFailure(record *kgo.Record, env Envelope, msg string, err error) {
	slog.Error("kafka: "+msg,
		"event_id", env.EventID,
		"event_type", env.EventType,
		"topic", record.Topic,
		"partition", record.Partition,
		"offset", record.Offset,
		"key", string(record.Key),
		"correlation_id", env.CorrelationID,
		"error", err,
	)
}
