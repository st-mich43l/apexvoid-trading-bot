package engine_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

var testMomentum = momentum.Config{Lookback: 8, BullThreshold: 0.15, BearThreshold: -0.15}

func confirmedFrame(tf market.Timeframe, open int64, trend structure.TrendState) *context.TimeframeContext {
	return &context.TimeframeContext{
		Timeframe: tf,
		Candles:   []market.Candle{{Time: open}},
		Structure: structure.StructureState{Major: structure.LayerState{
			Layer: structure.StructureMajor, Trend: trend,
		}},
	}
}

func TestClosedHigherTimeframeBiases_PreservesH1H4Disagreement(t *testing.T) {
	const base int64 = 86400
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.H1: confirmedFrame(market.H1, base+3600, structure.TrendBearish),
		market.H4: confirmedFrame(market.H4, base-14400, structure.TrendBullish),
	}}
	got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900, testMomentum)
	if len(got) != 2 || got[0].Timeframe != market.H1 || got[0].Direction != market.Sell || got[1].Timeframe != market.H4 || got[1].Direction != market.Buy {
		t.Fatalf("preserve independent causal H1/H4 directions; got %+v", got)
	}
}

func TestClosedHigherTimeframeBiases_AppendsM15AsCausalFallback(t *testing.T) {
	const base int64 = 86400
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.M15: confirmedFrame(market.M15, base+6300, structure.TrendBullish),
	}}
	got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900, testMomentum)
	if len(got) != 1 || got[0].Timeframe != market.M15 || got[0].Direction != market.Buy {
		t.Fatalf("fresh M15 structure must remain available when H1/H4 are absent, got %+v", got)
	}
}

func TestClosedHigherTimeframeBiases_ExcludesUnclosedAndStaleBars(t *testing.T) {
	const base int64 = 86400
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.H1: confirmedFrame(market.H1, base+6900, structure.TrendBullish),
		market.H4: confirmedFrame(market.H4, base-60000, structure.TrendBearish),
	}}
	got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900, testMomentum)
	if len(got) != 0 {
		t.Fatalf("future H1 close and stale H4 must be omitted, got %+v", got)
	}
}

func TestClosedHigherTimeframeBiases_NeverGuessesUnconfirmedStructure(t *testing.T) {
	const base int64 = 86400
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.H1: confirmedFrame(market.H1, base+3600, structure.TrendRange),
	}}
	if got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900, testMomentum); len(got) != 0 {
		t.Fatalf("no confirmed trend must not become guessed HTF bias, got %+v", got)
	}
}

// trendingFrame is an H1 frame whose structure is undecided (range) but whose
// closes trend steadily: `step` per bar, ending with the bar that opens at
// `lastOpen`.
func trendingFrame(tf market.Timeframe, lastOpen int64, step float64) *context.TimeframeContext {
	const bars = 40
	candles := make([]market.Candle, bars)
	for i := range candles {
		p := 1000 + float64(i)*step
		candles[i] = market.Candle{
			Time: lastOpen - int64(bars-1-i)*3600, Open: p - step/2, High: p + 1, Low: p - 1, Close: p,
		}
	}
	return &context.TimeframeContext{
		Timeframe: tf, Candles: candles,
		Structure: structure.StructureState{Major: structure.LayerState{Layer: structure.StructureMajor, Trend: structure.TrendRange}},
	}
}

func TestClosedHigherTimeframeBiases_UndecidedStructureFallsBackToHigherTimeframeMomentum(t *testing.T) {
	const base int64 = 86400
	for name, tc := range map[string]struct {
		step float64
		want market.Direction
	}{"rising": {2, market.Buy}, "falling": {-2, market.Sell}} {
		ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
			market.H1: trendingFrame(market.H1, base+3600, tc.step),
		}}
		got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900, testMomentum)
		if len(got) != 1 || got[0].Timeframe != market.H1 || got[0].Direction != tc.want || got[0].Layer != "momentum" || got[0].ReferenceTime != base+3600 {
			t.Fatalf("%s: undecided structure + %v momentum should emit a momentum-layer H1 bias, got %+v", name, tc.want, got)
		}
	}
}

func TestClosedHigherTimeframeBiases_NeutralMomentumStillOmitsUndecidedStructure(t *testing.T) {
	const base int64 = 86400
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.H1: trendingFrame(market.H1, base+3600, 0), // flat closes: velocity 0
	}}
	if got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900, testMomentum); len(got) != 0 {
		t.Fatalf("neutral momentum must not manufacture a bias, got %+v", got)
	}
}

func TestClosedHigherTimeframeBiases_DecidedStructureIsNeverOverriddenByMomentum(t *testing.T) {
	const base int64 = 86400
	frame := trendingFrame(market.H1, base+3600, 2) // strongly rising closes
	frame.Structure = structure.StructureState{Major: structure.LayerState{Layer: structure.StructureMajor, Trend: structure.TrendBearish}}
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{market.H1: frame}}
	got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900, testMomentum)
	if len(got) != 1 || got[0].Direction != market.Sell || got[0].Layer == "momentum" {
		t.Fatalf("a decided bearish structure must win over rising momentum, got %+v", got)
	}
}

func TestClosedHigherTimeframeBiases_MomentumFallbackStillRespectsStaleness(t *testing.T) {
	const base int64 = 86400
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.H1: trendingFrame(market.H1, base-60000, 2), // >2 H1 bars stale
	}}
	if got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900, testMomentum); len(got) != 0 {
		t.Fatalf("a stale higher-timeframe frame must be omitted even with strong momentum, got %+v", got)
	}
}
