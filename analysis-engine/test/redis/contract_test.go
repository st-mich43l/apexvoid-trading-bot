package redis_test

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"

	redisv9 "github.com/redis/go-redis/v9"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/barrier"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
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

func TestZoneBookKeyIsUppercaseAndNamespaced(t *testing.T) {
	if got, want := redistransport.ZoneBookKey("xau"), "analysis:zone_book:XAU"; got != want {
		t.Fatalf("ZoneBookKey(%q) = %q, want %q", "xau", got, want)
	}
}

func TestBuildZoneBook_FlattensSupplyDemandZonesAcrossTimeframesAndSkipsOtherKinds(t *testing.T) {
	zones := map[market.Timeframe]zone.ZoneState{
		market.M15: {ATR: 4.0, Zones: []zone.Zone{
			{ID: "z1", Kind: zone.KindSupply, Low: 2020, High: 2025, Strength: 0.8, TouchCount: 2, State: zone.StateFresh},
			{ID: "z2", Kind: zone.KindDemand, Low: 1990, High: 1995, Strength: 0.6, TouchCount: 1, State: zone.StateTouched},
		}},
		market.H1: {ATR: 8.0, Zones: []zone.Zone{
			{ID: "z3", Kind: zone.KindSupply, Low: 2030, High: 2040, Strength: 0.9, TouchCount: 0, State: zone.StateInvalidated},
		}},
	}
	doc := redistransport.BuildZoneBook("XAU", zones, 1700000100, nil)
	if doc.Symbol != "XAU" || doc.GeneratedAt != 1700000100 {
		t.Fatalf("unexpected document header: %+v", doc)
	}
	if len(doc.Entries) != 3 {
		t.Fatalf("expected 3 entries (every zone, kind and state included — filtering is the reader's job), got %d: %+v", len(doc.Entries), doc.Entries)
	}
	byID := map[string]redistransport.ZoneBookEntry{}
	for _, e := range doc.Entries {
		byID[e.Timeframe+":"+e.Kind+":"+e.State] = e
	}
	m15Supply, ok := byID["M15:supply:fresh"]
	if !ok {
		t.Fatalf("missing M15 supply/fresh entry in %+v", doc.Entries)
	}
	if m15Supply.Low != 2020 || m15Supply.High != 2025 || m15Supply.ATR != 4.0 || m15Supply.Strength != 0.8 || m15Supply.TouchCount != 2 {
		t.Errorf("unexpected M15 supply entry: %+v", m15Supply)
	}
	if _, ok := byID["H1:supply:invalidated"]; !ok {
		t.Fatalf("expected the invalidated H1 zone to still be published (the reader, not the publisher, decides what's a live barrier): %+v", doc.Entries)
	}
	// The document round-trips through JSON with exactly the field names a
	// Python reader keys off — a silent rename here breaks the contract.
	raw, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"symbol", "generated_at", "entries", "barriers"} {
		if _, ok := decoded[field]; !ok {
			t.Errorf("expected top-level JSON field %q, got %v", field, decoded)
		}
	}
}

func TestBuildZoneBook_BarriersAreNullWhenNoPolicyAndEmptyArrayWhenComputedEmpty(t *testing.T) {
	notComputed := redistransport.BuildZoneBook("XAU", map[market.Timeframe]zone.ZoneState{}, 1700000100, nil)
	if notComputed.Barriers != nil {
		t.Fatalf("no barrier policy must leave Barriers nil (not computed), got %#v", notComputed.Barriers)
	}
	raw, _ := json.Marshal(notComputed)
	if !strings.Contains(string(raw), `"barriers":null`) {
		t.Fatalf("not-computed barriers must serialize as null, got %s", raw)
	}
	cfg := &barrier.Config{PipSize: 0.1, MaxWidthATR: 2, MaxWidthPips: 100}
	computedEmpty := redistransport.BuildZoneBook("XAU", map[market.Timeframe]zone.ZoneState{}, 1700000100, cfg)
	raw, _ = json.Marshal(computedEmpty)
	if !strings.Contains(string(raw), `"barriers":[]`) {
		t.Fatalf("computed-but-empty barriers must serialize as [] so a reader can tell it from not-computed, got %s", raw)
	}
}

func TestBuildZoneBook_PublishesNormalizedBarriers(t *testing.T) {
	zones := map[market.Timeframe]zone.ZoneState{
		market.M1: {ATR: 1.0, Zones: []zone.Zone{
			{ID: "m1", Kind: zone.KindSupply, Low: 2020, High: 2021, Strength: 0.9, State: zone.StateFresh}, // M1 is out of scope
		}},
		market.M15: {ATR: 4.0, Zones: []zone.Zone{
			{ID: "s", Kind: zone.KindSupply, Low: 2020, High: 2025, Strength: 0.8, TouchCount: 2, State: zone.StatePartiallyMitigated},
			{ID: "dead", Kind: zone.KindDemand, Low: 1990, High: 1995, Strength: 0.6, State: zone.StateInvalidated},
			{ID: "wide", Kind: zone.KindDemand, Low: 1900, High: 1990, Strength: 0.6, State: zone.StateFresh}, // wider than 2 ATR
		}},
		market.H1: {ATR: 8.0, Zones: []zone.Zone{
			{ID: "s2", Kind: zone.KindSupply, Low: 2025, High: 2030, Strength: 0.95, TouchCount: 1, State: zone.StateTouched},
		}},
	}
	cfg := &barrier.Config{PipSize: 0.1, MaxWidthATR: 2, MaxWidthPips: 500}
	doc := redistransport.BuildZoneBook("XAU", zones, 1700000100, cfg)
	if len(doc.Entries) != 5 {
		t.Fatalf("entries stay unfiltered for readers that want raw zones: %d", len(doc.Entries))
	}
	if len(doc.Barriers) != 1 {
		t.Fatalf("live in-scope in-width supply zones touching at 2025 must merge into one barrier: %+v", doc.Barriers)
	}
	b := doc.Barriers[0]
	if b.Side != barrier.Sell || b.Low != 2020 || b.High != 2030 || b.Score != 0.95 || b.Touches != 1 ||
		len(b.SourceTimeframes) != 2 || b.SourceTimeframes[0] != "M15" || b.SourceTimeframes[1] != "H1" {
		t.Fatalf("unexpected merged barrier: %+v", b)
	}
}

func TestBuildZoneBook_EmptyWhenNoZonesTracked(t *testing.T) {
	doc := redistransport.BuildZoneBook("XAU", map[market.Timeframe]zone.ZoneState{}, 1700000100, nil)
	if len(doc.Entries) != 0 {
		t.Fatalf("expected no entries, got %+v", doc.Entries)
	}
}

// zoneBookTestClient mirrors test/integration/redis_pipeline_test.go's own
// redisTestClient — duplicated rather than shared because Go test helpers do
// not cross package/directory boundaries.
func zoneBookTestClient(t *testing.T) *redisv9.Client {
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

func TestPublishZoneBook_RealRedisRoundTrip(t *testing.T) {
	client := zoneBookTestClient(t)
	runtime, err := redistransport.NewRuntimeWithClient(
		client,
		redistransport.Config{URL: "redis://127.0.0.1:0/0", BarsChannel: "bars:new", ReconciliationInterval: time.Second},
		[]redistransport.Series{{Symbol: "XAU", Timeframe: market.M5, Depth: 10}},
		func(context.Context, marketdata.BarEvent) (marketdata.AppendResult, error) {
			return marketdata.AppendAccepted, nil
		},
		nil, nil,
	)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	key := redistransport.ZoneBookKey("XAU")
	t.Cleanup(func() { _ = client.Del(ctx, key).Err() })
	zones := map[market.Timeframe]zone.ZoneState{
		market.M15: {ATR: 4.0, Zones: []zone.Zone{{ID: "z1", Kind: zone.KindSupply, Low: 2020, High: 2025, Strength: 0.8, TouchCount: 2, State: zone.StateFresh}}},
	}
	if err := runtime.PublishZoneBook(ctx, "XAU", zones, time.Unix(1700000100, 0)); err != nil {
		t.Fatal(err)
	}
	raw, err := client.Get(ctx, key).Result()
	if err != nil {
		t.Fatalf("expected the zone book to be readable back from Redis: %v", err)
	}
	var doc redistransport.ZoneBookDTO
	if err := json.Unmarshal([]byte(raw), &doc); err != nil {
		t.Fatal(err)
	}
	if doc.Symbol != "XAU" || len(doc.Entries) != 1 || doc.Entries[0].Kind != "supply" {
		t.Fatalf("unexpected round-tripped document: %+v", doc)
	}
	ttl, err := client.TTL(ctx, key).Result()
	if err != nil {
		t.Fatal(err)
	}
	if ttl <= 0 || ttl > 20*time.Minute {
		t.Errorf("expected a positive TTL of at most 20m, got %v", ttl)
	}
}

func TestLiveOpportunitiesKeyIsUppercaseAndNamespaced(t *testing.T) {
	if got, want := redistransport.LiveOpportunitiesKey("xau"), "analysis:live_opportunities:XAU"; got != want {
		t.Fatalf("LiveOpportunitiesKey(%q) = %q, want %q", "xau", got, want)
	}
}

func TestBuildLiveOpportunities_SortsIDsAndNeverReturnsNil(t *testing.T) {
	doc := redistransport.BuildLiveOpportunities("XAU", []opportunity.Candidate{{ID: "opp_b"}, {ID: "opp_a"}, {ID: ""}}, 1700000100)
	if doc.Symbol != "XAU" || doc.GeneratedAt != 1700000100 {
		t.Fatalf("unexpected header: %+v", doc)
	}
	if len(doc.IDs) != 2 || doc.IDs[0] != "opp_a" || doc.IDs[1] != "opp_b" {
		t.Fatalf("expected sorted non-empty IDs, got %v", doc.IDs)
	}
	empty := redistransport.BuildLiveOpportunities("XAU", nil, 1)
	if empty.IDs == nil || len(empty.IDs) != 0 {
		t.Fatalf("an empty live set must serialise as [] not null, got %#v", empty.IDs)
	}
	raw, _ := json.Marshal(empty)
	if !strings.Contains(string(raw), `"ids":[]`) {
		t.Fatalf("expected ids:[] in %s", raw)
	}
}

func TestPublishLiveOpportunities_RealRedisRoundTrip(t *testing.T) {
	client := zoneBookTestClient(t)
	runtime, err := redistransport.NewRuntimeWithClient(
		client,
		redistransport.Config{URL: "redis://127.0.0.1:0/0", BarsChannel: "bars:new", ReconciliationInterval: time.Second},
		[]redistransport.Series{{Symbol: "XAU", Timeframe: market.M5, Depth: 10}},
		func(context.Context, marketdata.BarEvent) (marketdata.AppendResult, error) {
			return marketdata.AppendAccepted, nil
		},
		nil, nil,
	)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	key := redistransport.LiveOpportunitiesKey("XAU")
	t.Cleanup(func() { _ = client.Del(ctx, key).Err() })
	if err := runtime.PublishLiveOpportunities(ctx, "XAU", []opportunity.Candidate{{ID: "opp_1"}}, time.Unix(1700000100, 0)); err != nil {
		t.Fatal(err)
	}
	raw, err := client.Get(ctx, key).Result()
	if err != nil {
		t.Fatalf("expected the live set to be readable back from Redis: %v", err)
	}
	var doc redistransport.LiveOpportunitiesDTO
	if err := json.Unmarshal([]byte(raw), &doc); err != nil {
		t.Fatal(err)
	}
	if doc.Symbol != "XAU" || len(doc.IDs) != 1 || doc.IDs[0] != "opp_1" {
		t.Fatalf("unexpected round-tripped document: %+v", doc)
	}
	ttl, err := client.TTL(ctx, key).Result()
	if err != nil {
		t.Fatal(err)
	}
	if ttl <= 0 || ttl > 20*time.Minute {
		t.Errorf("expected a positive TTL of at most 20m, got %v", ttl)
	}
}
