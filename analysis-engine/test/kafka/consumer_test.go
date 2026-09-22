package kafka_test

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// TestConsumer_SuccessfulHandlerCommitsAndDoesNotRedeliverOnRestart
// proves source task §55's first case ("successful handler ->
// committable") end to end: a fresh consumer group joining after a
// clean run sees nothing left to redeliver.
func TestConsumer_SuccessfulHandlerCommitsAndDoesNotRedeliverOnRestart(t *testing.T) {
	base := uniqueTopic("consumer-ok")
	cfg := testConfig(t, base)
	publishRaw(t, cfg.Brokers, cfg.Topics.MarketBarClosed, "XAU", barClosedEnvelope(t, cfg.Topics.MarketBarClosed, testBarPayload("XAU", 100)))

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	handler := &testHandler{}
	consumer, err := kafka.NewConsumer(ctx, cfg, handler, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer: %v", err)
	}
	runCtx, runCancel := context.WithTimeout(ctx, 6*time.Second)
	_ = consumer.Run(runCtx)
	runCancel()

	if handler.barCount() != 1 {
		t.Fatalf("expected exactly 1 bar handled, got %d", handler.barCount())
	}

	// Restart with the SAME consumer group, a FRESH handler — a clean
	// commit means nothing is redelivered.
	handler2 := &testHandler{}
	consumer2, err := kafka.NewConsumer(ctx, cfg, handler2, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer (restart): %v", err)
	}
	runCtx2, runCancel2 := context.WithTimeout(ctx, 4*time.Second)
	_ = consumer2.Run(runCtx2)
	runCancel2()
	if handler2.barCount() != 0 {
		t.Errorf("expected no redelivery after a clean commit, but handled %d bars", handler2.barCount())
	}
}

// TestConsumer_PermanentHandlerFailureCommitsPastItWithoutRetrying
// proves source task §19/§55: a permanent (poison-message-equivalent)
// handler failure is never retried and still commits past the record.
func TestConsumer_PermanentHandlerFailureCommitsPastItWithoutRetrying(t *testing.T) {
	base := uniqueTopic("consumer-permanent")
	cfg := testConfig(t, base)
	publishRaw(t, cfg.Brokers, cfg.Topics.MarketBarClosed, "XAU", barClosedEnvelope(t, cfg.Topics.MarketBarClosed, testBarPayload("XAU", 100)))

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	handler := &testHandler{failFirstN: 1, failErr: func() error { return kafka.Permanent(fmt.Errorf("simulated permanent failure")) }}
	consumer, err := kafka.NewConsumer(ctx, cfg, handler, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer: %v", err)
	}
	runCtx, runCancel := context.WithTimeout(ctx, 6*time.Second)
	err = consumer.Run(runCtx)
	runCancel()
	if err != nil {
		t.Errorf("expected Run to complete cleanly (a permanent failure must never stall the consumer), got: %v", err)
	}
	if handler.barCount() != 0 {
		t.Errorf("the only record always failed permanently — expected 0 successfully-handled bars, got %d", handler.barCount())
	}

	// A permanent failure still commits past the record — restart with
	// a handler that WOULD succeed and confirm nothing is redelivered.
	handler2 := &testHandler{}
	consumer2, err := kafka.NewConsumer(ctx, cfg, handler2, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer (restart): %v", err)
	}
	runCtx2, runCancel2 := context.WithTimeout(ctx, 4*time.Second)
	_ = consumer2.Run(runCtx2)
	runCancel2()
	if handler2.barCount() != 0 {
		t.Errorf("expected the permanently-failed record to have been committed past (no redelivery), but handled %d bars", handler2.barCount())
	}
}

// TestConsumer_TransientFailureRecoversWithinTheRetryBudget proves a
// handler failing once with a Transient classification still succeeds
// (and commits) once the bounded retry resolves it.
func TestConsumer_TransientFailureRecoversWithinTheRetryBudget(t *testing.T) {
	base := uniqueTopic("consumer-transient-ok")
	cfg := testConfig(t, base)
	publishRaw(t, cfg.Brokers, cfg.Topics.MarketBarClosed, "XAU", barClosedEnvelope(t, cfg.Topics.MarketBarClosed, testBarPayload("XAU", 100)))

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	handler := &testHandler{failFirstN: 1, failErr: func() error { return kafka.Transient(fmt.Errorf("simulated transient blip")) }}
	consumer, err := kafka.NewConsumer(ctx, cfg, handler, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer: %v", err)
	}
	runCtx, runCancel := context.WithTimeout(ctx, 8*time.Second)
	err = consumer.Run(runCtx)
	runCancel()
	if err != nil {
		t.Errorf("expected the transient failure to resolve within the retry budget, got Run error: %v", err)
	}
	if handler.barCount() != 1 {
		t.Errorf("expected the bar to be successfully handled on retry, got %d", handler.barCount())
	}
}

// TestConsumer_TransientFailureExhaustingRetriesLeavesTheOffsetUncommitted
// proves source task §18's core guarantee: a handler that never
// recovers within the bounded retry budget must never be silently
// skipped — Run signals failure, and the record is redelivered.
func TestConsumer_TransientFailureExhaustingRetriesLeavesTheOffsetUncommitted(t *testing.T) {
	base := uniqueTopic("consumer-transient-exhausted")
	cfg := testConfig(t, base)
	publishRaw(t, cfg.Brokers, cfg.Topics.MarketBarClosed, "XAU", barClosedEnvelope(t, cfg.Topics.MarketBarClosed, testBarPayload("XAU", 100)))

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	handler := &testHandler{failFirstN: 1000, failErr: func() error { return kafka.Transient(fmt.Errorf("simulated persistent transient failure")) }}
	consumer, err := kafka.NewConsumer(ctx, cfg, handler, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer: %v", err)
	}
	runCtx, runCancel := context.WithTimeout(ctx, 8*time.Second)
	err = consumer.Run(runCtx)
	runCancel()
	if err == nil {
		t.Fatal("expected Run to return an error once the transient retry budget is exhausted (source task §18: never silently skip real, still-failing data)")
	}

	// Restart: the record was never committed, so it must be redelivered.
	handler2 := &testHandler{}
	consumer2, err := kafka.NewConsumer(ctx, cfg, handler2, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer (restart): %v", err)
	}
	runCtx2, runCancel2 := context.WithTimeout(ctx, 6*time.Second)
	_ = consumer2.Run(runCtx2)
	runCancel2()
	if handler2.barCount() != 1 {
		t.Errorf("expected the never-committed record to be redelivered exactly once, got %d", handler2.barCount())
	}
}

// TestConsumer_MalformedPayloadCommitsPastItAsAPermanentFailure proves
// source task §19's poison-message handling for the decode layer
// specifically: malformed JSON is never retried and never blocks
// subsequent processing.
func TestConsumer_MalformedPayloadCommitsPastItAsAPermanentFailure(t *testing.T) {
	base := uniqueTopic("consumer-malformed")
	cfg := testConfig(t, base)

	// A structurally invalid envelope: not valid JSON at all.
	client := testBrokers(t)
	publishRawBytes(t, client, cfg.Topics.MarketBarClosed, "XAU", []byte("{not json"))

	handler := &testHandler{}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	consumer, err := kafka.NewConsumer(ctx, cfg, handler, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer: %v", err)
	}
	runCtx, runCancel := context.WithTimeout(ctx, 6*time.Second)
	err = consumer.Run(runCtx)
	runCancel()
	if err != nil {
		t.Errorf("expected Run to complete cleanly past a malformed record, got: %v", err)
	}
	if handler.barCount() != 0 {
		t.Errorf("a malformed record must never reach the handler, got %d calls", handler.barCount())
	}

	// It must have been committed past, not redelivered.
	handler2 := &testHandler{}
	consumer2, err := kafka.NewConsumer(ctx, cfg, handler2, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer (restart): %v", err)
	}
	runCtx2, runCancel2 := context.WithTimeout(ctx, 4*time.Second)
	_ = consumer2.Run(runCtx2)
	runCancel2()
	if handler2.barCount() != 0 {
		t.Errorf("expected no redelivery, got %d", handler2.barCount())
	}
}

func TestNewConsumer_FailsClosedWhenNoBrokerIsReachable(t *testing.T) {
	testBrokers(t)
	cfg := testConfig(t, uniqueTopic("consumer-unreachable"))
	cfg.Brokers = []string{"127.0.0.1:1"}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	_, err := kafka.NewConsumer(ctx, cfg, &testHandler{}, nil, nil)
	if err == nil {
		t.Fatal("expected NewConsumer to fail closed against an unreachable broker (source task §43)")
	}
}

func TestNewConsumer_RejectsANilHandler(t *testing.T) {
	base := uniqueTopic("consumer-nilhandler")
	cfg := testConfig(t, base)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if _, err := kafka.NewConsumer(ctx, cfg, nil, nil, nil); err == nil {
		t.Fatal("expected NewConsumer to reject a nil Handler")
	}
}
