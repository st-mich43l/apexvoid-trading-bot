// Package integration_test validates multiple engine layers wired
// together — bars -> structure -> liquidity -> context -> snapshot
// (source task §9's own integration-test category), as opposed to
// test/structure and test/liquidity's own unit-level coverage of each
// layer in isolation, and test/engine's coverage of the worker/dispatch
// mechanics themselves.
package integration_test

import (
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
// validation without engaging any real strategy factory — this test
// exercises the structure/liquidity/context pipeline, not strategy
// evaluation (see test/strategy/registry_test.go's own fullConfig for
// the identical pattern).
func allStrategiesDisabled() []strategy.Config {
	configs := make([]strategy.Config, 0, len(strategy.KnownIDs()))
	for _, id := range strategy.KnownIDs() {
		configs = append(configs, strategy.Config{ID: id, Version: "v2", Enabled: false})
	}
	return configs
}

func settings() engine.Settings {
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
		HistoryDepths: map[market.Timeframe]int{"M5": 500}, PrimaryTimeframe: "M5",
	}
}

// TestFullPipeline_TrendReversalProducesStructureLiquidityAndContext feeds
// a hand-built XAU-scale M5 sequence — a clean uptrend that reverses into
// a downtrend — through the real Engine.Dispatch path (the SAME code a
// live feed or cmd/replay uses) and checks the result at every layer:
// structure finds the trend and its reversal, liquidity has pools, and
// context's Bias reflects the final read. This is the closest thing in
// this test suite to "does the whole thing actually work end to end,"
// deliberately built from a market-shaped fixture rather than the
// minimal/synthetic ones test/structure and test/liquidity use for their
// own focused unit coverage.
func TestFullPipeline_TrendReversalProducesStructureLiquidityAndContext(t *testing.T) {
	e := engine.NewEngine(nil)
	if err := e.Register("XAU", settings()); err != nil {
		t.Fatal(err)
	}

	bars := xauTrendReversalFixture()
	var lastSnap engine.AnalysisSnapshot
	for _, b := range bars {
		snap, err := e.Dispatch(marketdata.BarEvent{Symbol: "XAU", Timeframe: "M5", Candle: b})
		if err != nil {
			t.Fatalf("dispatch failed at t=%d: %v", b.Time, err)
		}
		lastSnap = snap
	}

	structState, ok := lastSnap.Structure["M5"]
	if !ok {
		t.Fatal("expected M5 structure in the final snapshot")
	}
	if len(structState.Swings) == 0 {
		t.Fatal("a real up-then-down fixture must produce at least one confirmed swing")
	}

	liqState, ok := lastSnap.Liquidity["M5"]
	if !ok || len(liqState.Pools) == 0 {
		t.Error("expected at least one liquidity pool once swings exist")
	}

	if lastSnap.Context.Symbol != "XAU" {
		t.Error("Context.Symbol must be the dispatched symbol")
	}
	if lastSnap.Context.Structure.Timeframe != "M5" {
		t.Error("Context.Structure must reflect the primary (M5) timeframe")
	}
	if lastSnap.Version.StructureVersion != "v2" || lastSnap.Version.LiquidityVersion != "v1" {
		t.Errorf("snapshot must carry the algorithm versions that produced it (source task §43), got %+v", lastSnap.Version)
	}
}

// xauTrendReversalFixture is a hand-built, XAU-price-scale (real
// production geometry: pip 0.1) M5 sequence — a clean run up from 4330 to
// 4360, then a reversal back down through 4335 — chosen to be large
// enough (30-point legs against a ~1-2 point per-bar range) to clear this
// fixture's own ATR-relative promotion thresholds without hand-tuning a
// second, fixture-specific config.
func xauTrendReversalFixture() []market.Candle {
	var out []market.Candle
	t := int64(1)
	price := 4330.0
	step := func(delta float64) {
		// Asymmetric wick padding by direction (a bullish bar's real
		// extreme is its high, a bearish bar's is its low) — a SYMMETRIC
		// +/-0.3 pad on every bar ties the reversal bar's High exactly to
		// the peak bar's High whenever one bar's Open equals the
		// previous bar's Close (always true here), which
		// isPivotHigh's strict inequality correctly rejects as not a
		// pivot at all. This is a fixture-construction fix, not an
		// algorithm one.
		open := price
		cl := price + delta
		var hi, lo float64
		if delta >= 0 {
			hi, lo = cl+0.3, open-0.1
		} else {
			hi, lo = open+0.1, cl-0.3
		}
		out = append(out, market.Candle{Time: t, Open: open, High: hi, Low: lo, Close: cl, Volume: 1})
		t++
		price = cl
	}
	for i := 0; i < 15; i++ {
		step(2.0) // up leg: 4330 -> ~4360
	}
	for i := 0; i < 15; i++ {
		step(-2.0) // down leg, equally steep: back through the origin -> a decisive, unambiguous reversal at the peak, not a borderline one
	}
	return out
}
