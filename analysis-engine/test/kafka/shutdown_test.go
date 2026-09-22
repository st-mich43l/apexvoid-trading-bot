package kafka_test

import (
	"context"
	"runtime"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// TestConsumer_Run_ExitsPromptlyOnContextCancellation proves source
// task §37's shutdown model directly: cancelling ctx is the sole
// shutdown signal, and Run returns well within a bounded window rather
// than hanging indefinitely.
func TestConsumer_Run_ExitsPromptlyOnContextCancellation(t *testing.T) {
	base := uniqueTopic("shutdown-consumer")
	cfg := testConfig(t, base)

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	consumer, err := kafka.NewConsumer(ctx, cfg, &testHandler{}, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer: %v", err)
	}

	runCtx, runCancel := context.WithCancel(ctx)
	done := make(chan error, 1)
	go func() { done <- consumer.Run(runCtx) }()

	// Let the consumer actually start polling before cancelling —
	// otherwise this proves nothing about a mid-poll shutdown.
	time.Sleep(500 * time.Millisecond)
	shutdownStart := time.Now()
	runCancel()

	select {
	case err := <-done:
		if err != nil {
			t.Errorf("expected a clean shutdown (nil error) on context cancellation, got: %v", err)
		}
		if elapsed := time.Since(shutdownStart); elapsed > 5*time.Second {
			t.Errorf("expected Run to exit within a bounded window of cancellation, took %v", elapsed)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("Run did not exit within 10s of context cancellation — shutdown hung")
	}
}

// TestProducer_Close_FlushesAndClosesWithinItsTimeout proves the
// producer side of source task §37 — Close does not hang, and a
// publish issued just before Close is not silently lost.
func TestProducer_Close_FlushesAndClosesWithinItsTimeout(t *testing.T) {
	base := uniqueTopic("shutdown-producer")
	cfg := testConfig(t, base)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	producer, err := kafka.NewProducer(ctx, cfg, kafka.ConfigProvenance{}, nil, nil)
	if err != nil {
		t.Fatalf("NewProducer: %v", err)
	}
	if err := producer.PublishOpportunity(ctx, "corr", "", testCandidate(), kafka.AlgorithmVersion{}, time.Unix(1, 0)); err != nil {
		t.Fatalf("PublishOpportunity: %v", err)
	}

	closeCtx, closeCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer closeCancel()
	closeStart := time.Now()
	if err := producer.Close(closeCtx); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if elapsed := time.Since(closeStart); elapsed > 10*time.Second {
		t.Errorf("expected Close to complete within its own timeout budget, took %v", elapsed)
	}

	// The record published before Close must have actually reached the
	// broker (Close flushes first, per ADR-008) — not silently dropped.
	verifyCtx, verifyCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer verifyCancel()
	consumeOne(t, cfg.Brokers, cfg.Topics.AnalysisOpportunity, verifyCtx)
}

// TestConsumer_Run_DoesNotLeakGoroutinesAcrossRepeatedStartStop is a
// best-effort check (source task §57's "no goroutine leak," run under
// go test -race): after several consumer start/stop cycles, the
// goroutine count returns to roughly its starting point rather than
// growing unbounded.
func TestConsumer_Run_DoesNotLeakGoroutinesAcrossRepeatedStartStop(t *testing.T) {
	base := uniqueTopic("shutdown-leak")
	cfg := testConfig(t, base)

	runtime.GC()
	time.Sleep(100 * time.Millisecond)
	before := runtime.NumGoroutine()

	for i := 0; i < 5; i++ {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		consumer, err := kafka.NewConsumer(ctx, cfg, &testHandler{}, nil, nil)
		if err != nil {
			cancel()
			t.Fatalf("NewConsumer (iteration %d): %v", i, err)
		}
		runCtx, runCancel := context.WithCancel(ctx)
		done := make(chan struct{})
		go func() { consumer.Run(runCtx); close(done) }()
		time.Sleep(200 * time.Millisecond)
		runCancel()
		<-done
		cancel()
	}

	runtime.GC()
	time.Sleep(300 * time.Millisecond)
	after := runtime.NumGoroutine()

	// Generous tolerance — this is a leak smoke test, not an exact
	// accounting; the goal is catching an unbounded per-iteration
	// growth, not asserting a specific stable count.
	if after > before+10 {
		t.Errorf("goroutine count grew from %d to %d across 5 start/stop cycles — possible leak", before, after)
	}
}
