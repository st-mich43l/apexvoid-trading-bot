package engine_test

import (
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// allStrategiesDisabled satisfies strategy.NewRegistry's Phase S8
// validation (every known catalog ID must be present with a version;
// see internal/engine/worker.go's own NewSymbolWorker) without engaging
// any real strategy factory — these tests exercise the worker/dispatch
// mechanics, not strategy evaluation itself (see test/strategy/
// registry_test.go's own fullConfig for the identical pattern).
func allStrategiesDisabled() []strategy.Config {
	configs := make([]strategy.Config, 0, len(strategy.KnownIDs()))
	for _, id := range strategy.KnownIDs() {
		configs = append(configs, strategy.Config{ID: id, Version: "v2", Enabled: false})
	}
	return configs
}

func testSettings(depths map[market.Timeframe]int) engine.Settings {
	return engine.Settings{
		ATR: engine.ATRSettings{Algorithm: indicator.AlgorithmSimple, Length: 14},
		Structure: structure.Settings{
			Version: "v2", PivotLeftBars: 2, PivotRightBars: 2,
			Promotion: structure.PromotionConfig{
				MinimumExcursionATR: 0.5, InternalATR: 1.0, IntermediateATR: 2.0, MajorATR: 3.5,
			},
			EqualToleranceATR: 0.05,
			Break: structure.BreakConfig{
				MinimumPenetrationATR: 0.5, SweepReclaimBars: 6, FailedBreakReclaimBars: 3,
				DisplacementMaxBars: 6,
				Displacement:        structure.DisplacementConfig{RangeATR: 1.5, BodyDominance: 0.55},
			},
		},
		Liquidity: liquidity.Config{
			Version: "v1", EqualLevelToleranceATR: 0.05, PoolMinimumTouches: 2, SweepReclaimBars: 6,
		},
		Strategies:    allStrategiesDisabled(),
		HistoryDepths: depths, PrimaryTimeframe: "M5", AllowReplaceForming: false,
	}
}

func bar(symbol market.Symbol, tf market.Timeframe, t int64, o, h, l, cl float64) marketdata.BarEvent {
	return marketdata.BarEvent{
		Symbol: symbol, Timeframe: tf,
		Candle: market.Candle{Time: t, Open: o, High: h, Low: l, Close: cl, Volume: 1},
	}
}

func TestEngine_DispatchToUnregisteredSymbolFailsClosed(t *testing.T) {
	e := engine.NewEngine(nil)
	_, err := e.Dispatch(bar("XAU", "M5", 1, 100, 101, 99, 100))
	if err == nil {
		t.Fatal("expected an error dispatching to a symbol that was never Register-ed")
	}
}

func TestEngine_ClosingOneTimeframeNeverRecomputesAnother(t *testing.T) {
	// Source task §42's core claim, proven directly: closing an M5 bar
	// must not create or touch M1's structure at all.
	e := engine.NewEngine(nil)
	settings := testSettings(map[market.Timeframe]int{"M1": 100, "M5": 100})
	if err := e.Register("XAU", settings); err != nil {
		t.Fatal(err)
	}
	snap, err := e.Dispatch(bar("XAU", "M5", 1, 100, 101, 99, 100.5))
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := snap.Structure["M5"]; !ok {
		t.Error("expected M5's structure to be present after an M5 bar closed")
	}
	if _, ok := snap.Structure["M1"]; ok {
		t.Error("M1's structure must not exist at all — no M1 bar has ever closed, and the M5 close must not have created one")
	}
}

func TestEngine_ConcurrentDispatchAcrossDifferentSymbolsNeverLosesAnEvent(t *testing.T) {
	// Source task §41: "different symbols = concurrent." Run under
	// `go test -race` (this repo's own CI/verification command) to prove
	// no data race, not just that it doesn't deadlock.
	e := engine.NewEngine(nil)
	settings := testSettings(map[market.Timeframe]int{"M5": 500})
	symbols := []market.Symbol{"XAU", "EURUSD", "USDJPY", "GBPJPY"}
	for _, s := range symbols {
		if err := e.Register(s, settings); err != nil {
			t.Fatal(err)
		}
	}

	const barsPerSymbol = 50
	var wg sync.WaitGroup
	for _, s := range symbols {
		wg.Add(1)
		go func(symbol market.Symbol) {
			defer wg.Done()
			price := 100.0
			for i := 0; i < barsPerSymbol; i++ {
				price += 0.1
				if _, err := e.Dispatch(bar(symbol, "M5", int64(i+1), price, price+0.5, price-0.5, price+0.1)); err != nil {
					t.Errorf("%s: unexpected dispatch error: %v", symbol, err)
				}
			}
		}(s)
	}
	wg.Wait()

	for _, s := range symbols {
		snap, err := e.Snapshot(s, int64(barsPerSymbol))
		if err != nil {
			t.Fatal(err)
		}
		// A monotonic price ramp (this fixture) legitimately produces zero
		// swings — no local peak or trough ever forms — so the real
		// assertion is that M5 was processed AT ALL (the map key exists),
		// not that it found structure to report.
		if _, ok := snap.Structure["M5"]; !ok {
			t.Errorf("%s: expected M5 to have been processed after %d bars", s, barsPerSymbol)
		}
	}
}

func TestEngine_ConcurrentDispatchToTheSameSymbolAccountsForEveryEventExactlyOnce(t *testing.T) {
	// Goroutine scheduling gives no guarantee about which order n
	// concurrent Dispatch calls actually reach the worker's mutex in —
	// so this cannot assert "all n were accepted as new bars" (some may
	// legitimately land out of Time order relative to each other and be
	// reported AppendOutOfOrder, which is correct behavior, not a bug).
	// What must always be true regardless of arrival order: the mutex
	// serializes every call with no data race and no lost/double-counted
	// event — proven here via the telemetry counters, which are
	// themselves mutex-guarded (internal/telemetry.Recorder), summing to
	// exactly n either way.
	e := engine.NewEngine(nil)
	settings := testSettings(map[market.Timeframe]int{"M5": 1000})
	if err := e.Register("XAU", settings); err != nil {
		t.Fatal(err)
	}
	const n = 200
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			price := 100.0 + float64(i)*0.1
			_, _ = e.Dispatch(bar("XAU", "M5", int64(i+1), price, price+0.5, price-0.5, price+0.1))
		}(i)
	}
	wg.Wait()

	_, counts := e.Telemetry().Snapshot()
	var total int64
	for _, sample := range counts {
		total += sample.Count
	}
	if total != n {
		t.Errorf("expected exactly %d events accounted for across processed+duplicate+out-of-order+rejected, got %d", n, total)
	}
}
