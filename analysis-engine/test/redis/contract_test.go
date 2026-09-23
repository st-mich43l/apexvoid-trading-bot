package redis_test

import (
	"context"
	"testing"
	"time"

	redisv9 "github.com/redis/go-redis/v9"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
)

func TestDecodeBar_ConsumesTheExactDotNetRedisBarJSON(t *testing.T) {
	series := redistransport.Series{Symbol: "XAU", Timeframe: market.M5, Depth: 1000}
	event, err := redistransport.DecodeBar(`{"t":1700000000,"o":2000.10,"h":2005.25,"l":1998.75,"c":2003.50,"v":120}`, series)
	if err != nil {
		t.Fatal(err)
	}
	if event.Symbol != "XAU" || event.Timeframe != market.M5 || event.Candle.Time != 1700000000 || event.Candle.Close != 2003.50 {
		t.Fatalf("unexpected event: %+v", event)
	}
	if _, err := redistransport.DecodeBar(`{"t":1,"o":1,"h":1,"l":1,"c":1,"v":1,"drift":true}`, series); err == nil {
		t.Fatal("expected strict Redis DTO decoder to reject unknown fields")
	}
}

func TestParseNotificationUsesExistingBarsNewFormat(t *testing.T) {
	n, err := redistransport.ParseNotification("XAU:M5:1700000000")
	if err != nil {
		t.Fatal(err)
	}
	if n.Symbol != "XAU" || n.Timeframe != market.M5 || n.Timestamp != 1700000000 {
		t.Fatalf("unexpected notification: %+v", n)
	}
	if _, err := redistransport.ParseNotification("XAU:M2:bad"); err == nil {
		t.Fatal("expected invalid notification to be rejected")
	}
}

func TestRedisConfigRequiresExplicitRecoverySettings(t *testing.T) {
	cfg := redistransport.Config{URL: "redis://redis:6379/0", BarsChannel: "bars:new", ReconciliationInterval: time.Second}
	if err := cfg.Validate(); err != nil {
		t.Fatal(err)
	}
	cfg.ReconciliationInterval = 0
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected a missing reconciliation interval to fail")
	}
}

func TestRuntime_RedisOutageKeepsProcessLiveAndReadinessFalse(t *testing.T) {
	client := redisv9.NewClient(&redisv9.Options{Addr: "127.0.0.1:1", DialTimeout: 10 * time.Millisecond})
	t.Cleanup(func() { _ = client.Close() })
	runtime, err := redistransport.NewRuntimeWithClient(
		client,
		redistransport.Config{URL: "redis://127.0.0.1:1/0", BarsChannel: "bars:new", ReconciliationInterval: time.Second},
		[]redistransport.Series{{Symbol: "XAU", Timeframe: market.M5, Depth: 10}},
		func(context.Context, marketdata.BarEvent) (marketdata.AppendResult, error) {
			return marketdata.AppendAccepted, nil
		},
		nil,
		nil,
	)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runtime.Run(ctx) }()
	select {
	case err := <-done:
		t.Fatalf("runtime exited during a Redis outage: %v", err)
	case <-time.After(100 * time.Millisecond):
	}
	if runtime.Health().Snapshot().Ready() {
		t.Fatal("Redis-unavailable runtime must not be ready")
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("runtime did not stop after cancellation")
	}
}
