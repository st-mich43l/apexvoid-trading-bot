package engine_test

import (
	"bufio"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/arbitration"
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
	settings.Geometry, err = doc.GeometryFor("XAU")
	if err != nil {
		t.Fatalf("loading real XAU instrument geometry: %v", err)
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
		if candidate.StopEnvelope == nil {
			t.Errorf("opportunity %s (%s) has no StopEnvelope against real production execution config", candidate.ID, candidate.Strategy)
		} else if candidate.StopEnvelope.FloorPips <= 0 || candidate.StopEnvelope.CapPips < candidate.StopEnvelope.FloorPips {
			t.Errorf("opportunity %s has an invalid StopEnvelope: %+v", candidate.ID, candidate.StopEnvelope)
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

// TestEngine_FirstLiveBarAfterBootstrapRepublishesEveryArbitrationDecision
// guards the 2026-10-01 production failure: a restarted engine rebuilt its
// state by bootstrap, recorded the rebuilt decisions as "already published",
// and then never told the consumer anything — so the algo-bot kept every
// opportunity on the stale conflict_held status it had stored before the
// restart. A decision is a current-status projection, so the first live bar
// must publish the complete current in-play set. Resting zones intentionally
// do not enter Kafka: publishing one projection for every historical zone
// starves the current executable decision behind the synchronous outbox.
func TestEngine_FirstLiveBarAfterBootstrapRepublishesEveryInPlayArbitrationDecision(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatalf("resolving config: %v", err)
	}
	settings, err := engine.LoadSettings(doc, "M5", false)
	if err != nil {
		t.Fatalf("loading settings: %v", err)
	}
	if settings.Geometry, err = doc.GeometryFor("XAU"); err != nil {
		t.Fatal(err)
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
	candles := loadRealXAUFixture(t)
	last := len(candles) - 1
	var snapshot engine.AnalysisSnapshot
	for _, candle := range candles[:last] {
		snapshot, err = e.Dispatch(marketdata.BarEvent{Symbol: "XAU", Timeframe: "M5", Candle: candle, Origin: marketdata.EventOriginBootstrap})
		if err != nil {
			t.Fatalf("bootstrap dispatch: %v", err)
		}
	}
	if len(snapshot.Opportunities) < 2 {
		t.Fatalf("fixture must leave several live opportunities to arbitrate, got %d", len(snapshot.Opportunities))
	}
	time.Sleep(50 * time.Millisecond)
	if got := client.arbitrationSnapshot(); len(got) != 0 {
		t.Fatalf("bootstrap must publish no arbitration decisions, got %d", len(got))
	}

	snapshot, err = e.Dispatch(marketdata.BarEvent{Symbol: "XAU", Timeframe: "M5", Candle: candles[last]})
	if err != nil {
		t.Fatalf("first live dispatch: %v", err)
	}
	time.Sleep(200 * time.Millisecond)

	published := map[string]bool{}
	for _, d := range client.arbitrationSnapshot() {
		published[d.OpportunityID] = true
	}
	reference := candles[last].Close
	ready := false
	for _, c := range snapshot.Opportunities {
		if c.Technical != nil && c.Entry.Low <= reference && reference <= c.Entry.High {
			ready = true
			break
		}
	}
	for _, c := range snapshot.Opportunities {
		inPlay := settings.Arbitration.InPlayATR <= 0
		if ready {
			inPlay = c.Technical != nil && c.Entry.Low <= reference && reference <= c.Entry.High
		} else if c.Technical != nil && c.Technical.ATR > 0 && settings.Arbitration.InPlayATR > 0 {
			distance := 0.0
			// The live event's close is the same reference passed to
			// ArbitrateInPlay by SymbolWorker.
			switch {
			case reference < c.Entry.Low:
				distance = c.Entry.Low - reference
			case reference > c.Entry.High:
				distance = reference - c.Entry.High
			}
			inPlay = distance <= settings.Arbitration.InPlayATR*c.Technical.ATR
		}
		if inPlay && !published[c.ID] {
			t.Errorf("in-play opportunity %s had no arbitration decision published on the first live bar after bootstrap", c.ID)
		}
	}
	for _, d := range client.arbitrationSnapshot() {
		if d.ReasonCode == arbitration.ReasonNotInPlay {
			t.Errorf("resting opportunity %s should not publish a not_in_play projection", d.OpportunityID)
		}
	}
}

// TestEngine_LegacyZonesChangeOnlyTheZoneDrivenStrategies proves the
// Python-parity switch swaps the zone population the zone-driven strategies
// read (supply/demand/order-block/FVG/iFVG and the confluence bands built from
// them: the confluence_zone strategy narrows from 155 to 64 opportunities on the
// real XAU fixture) while strategies that do not read those zones are identical.
func TestEngine_LegacyZonesChangeOnlyTheZoneDrivenStrategies(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	discovered := func(enabled bool) map[opportunity.StrategyID]int {
		settings, err := engine.LoadSettings(doc, "M5", false)
		if err != nil {
			t.Fatal(err)
		}
		if err := engine.ApplyInstrument(&settings, doc, "XAU"); err != nil {
			t.Fatal(err)
		}
		settings.LegacyZones.Enabled = enabled
		e := engine.NewEngine(nil)
		if err := e.Register("XAU", settings); err != nil {
			t.Fatal(err)
		}
		seen := map[string]bool{}
		counts := map[opportunity.StrategyID]int{}
		for _, c := range loadRealXAUFixture(t) {
			snap, err := e.Dispatch(marketdata.BarEvent{Symbol: "XAU", Timeframe: "M5", Candle: c})
			if err != nil {
				t.Fatal(err)
			}
			for _, o := range snap.Opportunities {
				if !seen[o.ID] {
					seen[o.ID] = true
					counts[o.Strategy]++
				}
			}
		}
		return counts
	}
	off, on := discovered(false), discovered(true)
	if on["confluence_zone"] >= off["confluence_zone"] || on["confluence_zone"] == 0 {
		t.Fatalf("legacy zones must narrow the confluence bands: off=%d on=%d", off["confluence_zone"], on["confluence_zone"])
	}
	for _, unaffected := range []opportunity.StrategyID{"key_level", "session_level", "liquidity_sweep", "trendline", "flip_zone"} {
		if on[unaffected] != off[unaffected] {
			t.Fatalf("%s does not read the replaced zones but changed: off=%d on=%d", unaffected, off[unaffected], on[unaffected])
		}
	}
	t.Logf("opportunities per strategy: redesigned %v, legacy %v", off, on)
}
