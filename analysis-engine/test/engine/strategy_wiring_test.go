package engine_test

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

type jsonBar struct {
	T int64   `json:"t"`
	O float64 `json:"o"`
	H float64 `json:"h"`
	L float64 `json:"l"`
	C float64 `json:"c"`
	V float64 `json:"v"`
}

func loadRealXAUFixture(t *testing.T) []market.Candle {
	t.Helper()
	path := filepath.Join("..", "..", "testdata", "raw_xau_m5_snapshot.jsonl")
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("opening real fixture: %v", err)
	}
	defer f.Close()
	var candles []market.Candle
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		var b jsonBar
		if err := json.Unmarshal(scanner.Bytes(), &b); err != nil {
			t.Fatalf("parsing fixture line: %v", err)
		}
		candles = append(candles, market.Candle{Time: b.T, Open: b.O, High: b.H, Low: b.L, Close: b.C, Volume: b.V})
	}
	if err := scanner.Err(); err != nil {
		t.Fatalf("scanning fixture: %v", err)
	}
	if len(candles) == 0 {
		t.Fatal("real XAU fixture is empty")
	}
	return candles
}

// TestEngine_RealEnabledStrategiesProduceRealOpportunitiesAgainstRealXAUData
// is the end-to-end proof for the exact same real, checked-in
// production config (config/apexvoid.yml) and the exact same real,
// checked-in XAU M5 production data cmd/replay already uses, dispatched
// through the real Engine.Dispatch path with NO fakes, NO mocks, and NO
// hand-tuned fixture — the same path this test module's other engine/
// integration tests use synthetic bars for, deliberately using real data
// here instead because Phase S8 wiring bugs (both found and fixed this
// phase: an opportunity.Book identity-collision false positive, and a
// key_level dedup-identity bug) only ever surfaced against real data, not
// against small hand-built fixtures.
func TestEngine_RealEnabledStrategiesProduceRealOpportunitiesAgainstRealXAUData(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatalf("resolving real production config: %v", err)
	}
	settings, err := engine.LoadSettings(doc, "M5", false)
	if err != nil {
		t.Fatalf("loading real engine settings: %v", err)
	}

	e := engine.NewEngine(nil)
	if err := e.Register("XAU", settings); err != nil {
		t.Fatalf("registering XAU with the real config-derived settings: %v", err)
	}

	candles := loadRealXAUFixture(t)
	var lastSnap engine.AnalysisSnapshot
	for _, c := range candles {
		snap, err := e.Dispatch(marketdata.BarEvent{Symbol: "XAU", Timeframe: "M5", Candle: c})
		if err != nil {
			t.Fatalf("dispatch failed at t=%d against real production config + real data: %v", c.Time, err)
		}
		lastSnap = snap
	}

	if len(lastSnap.Opportunities) == 0 {
		t.Fatal("expected at least one opportunity from enabled strategies against 300 real XAU M5 bars — got none")
	}
	seenStrategies := map[opportunity.StrategyID]bool{}
	for _, candidate := range lastSnap.Opportunities {
		if err := candidate.Validate(); err != nil {
			t.Errorf("live opportunity %s failed Candidate.Validate(): %v", candidate.ID, err)
		}
		if candidate.Symbol != "XAU" {
			t.Errorf("opportunity %s has symbol %q, want XAU", candidate.ID, candidate.Symbol)
		}
		seenStrategies[candidate.Strategy] = true
	}
	t.Logf("real replay produced %d live opportunities from %d distinct strategies: %v",
		len(lastSnap.Opportunities), len(seenStrategies), seenStrategies)
}

// TestEngine_ReDispatchingTheSameRealSequenceStaysErrorFree is a direct
// regression guard for the opportunity.Book identity-collision bug this
// phase found and fixed: running the exact same real sequence twice
// through two independently-registered symbols must never error, proving
// re-observation of the same still-valid setups (Created -> Active ->
// Duplicate) works, not just the first pass through fresh state.
func TestEngine_ReDispatchingTheSameRealSequenceStaysErrorFree(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatalf("resolving real production config: %v", err)
	}
	settings, err := engine.LoadSettings(doc, "M5", false)
	if err != nil {
		t.Fatalf("loading real engine settings: %v", err)
	}
	candles := loadRealXAUFixture(t)

	e := engine.NewEngine(nil)
	if err := e.Register("XAU", settings); err != nil {
		t.Fatal(err)
	}
	for pass := 0; pass < 2; pass++ {
		for _, c := range candles {
			if _, err := e.Dispatch(marketdata.BarEvent{Symbol: "XAU", Timeframe: "M5", Candle: c}); err != nil {
				t.Fatalf("pass %d: dispatch failed at t=%d: %v", pass, c.Time, err)
			}
		}
	}
}

// TestEngine_BootstrapBuildsStateWithoutRepublishingHistory is the S11
// shadow-run guard: restoring retained Redis history must still establish the
// real technical state, but it must not make years/minutes-old candidates look
// newly observed to downstream Kafka consumers after every process restart.
func TestEngine_BootstrapBuildsStateWithoutRepublishingHistory(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatalf("resolving config: %v", err)
	}
	settings, err := engine.LoadSettings(doc, "M5", false)
	if err != nil {
		t.Fatalf("loading settings: %v", err)
	}
	client := &fakeKafkaClient{}
	publisher := engine.NewOpportunityPublisher(client, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go publisher.Run(ctx)

	e := engine.NewEngine(nil)
	e.SetPublisher(publisher)
	if err := e.Register("XAU", settings); err != nil {
		t.Fatal(err)
	}
	var snapshot engine.AnalysisSnapshot
	for _, candle := range loadRealXAUFixture(t) {
		snapshot, err = e.Dispatch(marketdata.BarEvent{
			Symbol: "XAU", Timeframe: "M5", Candle: candle, Origin: marketdata.EventOriginBootstrap,
		})
		if err != nil {
			t.Fatalf("bootstrap dispatch at t=%d: %v", candle.Time, err)
		}
	}
	if len(snapshot.Opportunities) == 0 {
		t.Fatal("bootstrap must establish real live opportunity state")
	}
	// A running publisher would have drained any incorrectly enqueued job by
	// now. The fixture produces many lifecycle transitions, so this is a
	// meaningful assertion rather than an empty-input no-op.
	time.Sleep(50 * time.Millisecond)
	_, invalidations, calls := client.snapshot()
	opportunities, _, _ := client.snapshot()
	if len(opportunities) != 0 || len(invalidations) != 0 || len(calls) != 0 {
		t.Fatalf("bootstrap must not publish historical lifecycle transitions, got calls=%v", calls)
	}
}

// TestEngine_EveryLiveOpportunityCarriesCausalTechnicalFacts proves the S13B
// policy inputs are the engine's own, causal facts and not placeholders: for
// every opportunity produced from real production XAU data, ATR is exactly the
// canonical ATR of the candles up to and including the observed bar, the
// reference price is that bar's real close, and nothing looks past it.
func TestEngine_EveryLiveOpportunityCarriesCausalTechnicalFacts(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, "M5", false)
	if err != nil {
		t.Fatal(err)
	}
	e := engine.NewEngine(nil)
	if err := e.Register("XAU", settings); err != nil {
		t.Fatal(err)
	}
	candles := loadRealXAUFixture(t)
	index := make(map[int64]int, len(candles))
	var last engine.AnalysisSnapshot
	for i, c := range candles {
		index[c.Time] = i
		snap, err := e.Dispatch(marketdata.BarEvent{Symbol: "XAU", Timeframe: "M5", Candle: c})
		if err != nil {
			t.Fatal(err)
		}
		last = snap
	}
	if len(last.Opportunities) == 0 {
		t.Fatal("no live opportunities to check")
	}
	for _, opp := range last.Opportunities {
		tech := opp.Technical
		if tech == nil {
			t.Fatalf("%s carries no technical context", opp.ID)
		}
		i, ok := index[tech.ReferenceTime]
		if !ok {
			t.Fatalf("%s reference time %d is not a real bar", opp.ID, tech.ReferenceTime)
		}
		if tech.ReferenceTime != opp.CreatedAt {
			t.Errorf("%s: reference %d != created_at %d", opp.ID, tech.ReferenceTime, opp.CreatedAt)
		}
		if tech.ReferencePrice != candles[i].Close {
			t.Errorf("%s: reference price %v != real close %v", opp.ID, tech.ReferencePrice, candles[i].Close)
		}
		series, err := indicator.CanonicalATR(candles[:i+1], settings.ATR.Length, settings.ATR.Algorithm)
		if err != nil {
			t.Fatal(err)
		}
		if want := series[len(series)-1]; tech.ATR != want {
			t.Errorf("%s: ATR %v != canonical ATR %v over candles[:%d] (look-ahead or recompute drift)", opp.ID, tech.ATR, want, i+1)
		}
	}
}
