// Real Redis integration. Run with REDIS_TEST_URL=redis://host:6379/0;
// ordinary `go test ./...` skips it when a broker is not configured.
package integration_test

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	redisv9 "github.com/redis/go-redis/v9"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
)

func redisTestClient(t *testing.T) *redisv9.Client {
	t.Helper()
	raw := os.Getenv("REDIS_TEST_URL")
	if raw == "" {
		t.Skip("REDIS_TEST_URL not set — skipping real Redis integration")
	}
	options, err := redisv9.ParseURL(raw)
	if err != nil {
		t.Fatal(err)
	}
	client := redisv9.NewClient(options)
	if err := client.Ping(context.Background()).Err(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Close() })
	return client
}

func TestRedisRuntime_BootstrapNotificationRecoveryAndCorrections(t *testing.T) {
	client := redisTestClient(t)
	name := "XAUTEST" + strconv.FormatInt(time.Now().UnixNano(), 36)
	series := redistransport.Series{Symbol: market.Symbol(name), Timeframe: market.M5, Depth: 4}
	key := series.Key()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := client.Del(ctx, key).Err(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Del(context.Background(), key).Err() })

	write := func(timestamp int64, close float64) {
		t.Helper()
		if err := client.ZRemRangeByScore(ctx, key, strconv.FormatInt(timestamp, 10), strconv.FormatInt(timestamp, 10)).Err(); err != nil {
			t.Fatal(err)
		}
		value := fmt.Sprintf(`{"t":%d,"o":2000,"h":%.2f,"l":1998,"c":%.2f,"v":120}`, timestamp, close+5, close)
		if err := client.ZAdd(ctx, key, redisv9.Z{Score: float64(timestamp), Member: value}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	base := int64(1_700_000_000)
	write(base, 2001)
	write(base+300, 2002)
	write(base+600, 2003)

	history := marketdata.NewTimeframeHistory(series.Symbol, series.Timeframe, series.Depth, false)
	var mu sync.Mutex
	var results []marketdata.AppendResult
	runtime, err := redistransport.NewRuntimeWithClient(client,
		redistransport.Config{URL: os.Getenv("REDIS_TEST_URL"), BarsChannel: "bars:new", ReconciliationInterval: 100 * time.Millisecond},
		[]redistransport.Series{series},
		func(_ context.Context, event marketdata.BarEvent) (marketdata.AppendResult, error) {
			mu.Lock()
			defer mu.Unlock()
			result, err := history.Append(event.Candle)
			results = append(results, result)
			return result, err
		}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	spotKey := "price:" + strings.ToUpper(string(series.Symbol)) + ":spot"
	if err := client.Set(ctx, spotKey, `{"bid":2000.10,"ask":2000.30,"ts":1700001200}`, 0).Err(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Del(context.Background(), spotKey).Err() })
	spot, err := runtime.ReadSpot(ctx, series.Symbol)
	if err != nil {
		t.Fatal(err)
	}
	if spot.Bid != 2000.10 || spot.Ask != 2000.30 || spot.TS != 1700001200 {
		t.Fatalf("unexpected Redis spot: %+v", spot)
	}
	done := make(chan error, 1)
	go func() { done <- runtime.Run(ctx) }()
	defer func() {
		cancel()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Error("Redis runtime did not stop")
		}
		_ = runtime.Close()
	}()

	waitFor(t, 5*time.Second, func() bool {
		mu.Lock()
		defer mu.Unlock()
		return runtime.Health().Snapshot().Ready() && history.Len() == 3
	})
	// A normal notification triggers an authoritative ZSET read.
	write(base+900, 2004)
	if receivers, err := client.Publish(ctx, "bars:new", string(series.Symbol)+":M5:"+strconv.FormatInt(base+900, 10)).Result(); err != nil {
		t.Fatal(err)
	} else if receivers == 0 {
		t.Fatal("Redis runtime did not subscribe to bars:new")
	}
	waitFor(t, 5*time.Second, func() bool { mu.Lock(); defer mu.Unlock(); return history.Len() == 4 })
	// A missed notification is picked up by periodic reconciliation. The
	// bounded window evicts the oldest but preserves chronological advance.
	write(base+1200, 2005)
	waitFor(t, 5*time.Second, func() bool { mu.Lock(); defer mu.Unlock(); return history.Newest().Time == base+1200 })
	// Re-read same score then changed same score: domain AppendDuplicate and
	// AppendConflict are used, and neither silently rewrites history.
	if receivers, err := client.Publish(ctx, "bars:new", string(series.Symbol)+":M5:"+strconv.FormatInt(base+1200, 10)).Result(); err != nil {
		t.Fatal(err)
	} else if receivers == 0 {
		t.Fatal("Redis runtime lost its bars:new subscription")
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		counts, _ := runtime.Metrics().Snapshot()
		if counts[redistransport.MetricDuplicateBar] > 0 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	counts, _ := runtime.Metrics().Snapshot()
	if counts[redistransport.MetricDuplicateBar] == 0 {
		mu.Lock()
		defer mu.Unlock()
		t.Fatalf("notification did not produce a duplicate read: counts=%v results=%v cursor-history=%+v", counts, results, history.Newest())
	}
	write(base+1200, 2006)
	if receivers, err := client.Publish(ctx, "bars:new", string(series.Symbol)+":M5:"+strconv.FormatInt(base+1200, 10)).Result(); err != nil {
		t.Fatal(err)
	} else if receivers == 0 {
		t.Fatal("Redis runtime lost its bars:new subscription")
	}
	waitFor(t, 5*time.Second, func() bool {
		counts, _ := runtime.Metrics().Snapshot()
		return counts[redistransport.MetricDuplicateBar] > 0 && counts[redistransport.MetricConflictBar] > 0
	})
	mu.Lock()
	defer mu.Unlock()
	if history.Newest().Close != 2005 {
		t.Fatalf("conflicting corrected candle rewrote market history: %+v", history.Newest())
	}
	if len(results) < 6 {
		t.Fatalf("expected bootstrap, recovery, duplicate and conflict dispatches, got %v", results)
	}
}

func waitFor(t *testing.T, timeout time.Duration, predicate func() bool) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if predicate() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("timed out waiting for Redis runtime state")
}
