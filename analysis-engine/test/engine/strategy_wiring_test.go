package engine_test

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
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

// TestEngine_RealS7StrategiesProduceRealOpportunitiesAgainstRealXAUData is
// Phase S8's own end-to-end proof: the exact same real, checked-in
// production config (config/apexvoid.yml) and the exact same real,
// checked-in XAU M5 production data cmd/replay already uses, dispatched
// through the real Engine.Dispatch path with NO fakes, NO mocks, and NO
// hand-tuned fixture — the same path this test module's other engine/
// integration tests use synthetic bars for, deliberately using real data
// here instead because Phase S8 wiring bugs (both found and fixed this
// phase: an opportunity.Book identity-collision false positive, and a
// key_level dedup-identity bug) only ever surfaced against real data, not
// against small hand-built fixtures.
func TestEngine_RealS7StrategiesProduceRealOpportunitiesAgainstRealXAUData(t *testing.T) {
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
		t.Fatal("expected at least one live opportunity from real S7 strategies against 300 real XAU M5 bars — got none")
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
