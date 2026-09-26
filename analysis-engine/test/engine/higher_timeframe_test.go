package engine_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func confirmedFrame(tf market.Timeframe, open int64, trend structure.TrendState) *context.TimeframeContext {
	return &context.TimeframeContext{
		Timeframe: tf,
		Candles: []market.Candle{{Time: open}},
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
	got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900)
	if len(got) != 2 || got[0].Timeframe != market.H1 || got[0].Direction != market.Sell || got[1].Timeframe != market.H4 || got[1].Direction != market.Buy {
		t.Fatalf("preserve independent causal H1/H4 directions; got %+v", got)
	}
}

func TestClosedHigherTimeframeBiases_ExcludesUnclosedAndStaleBars(t *testing.T) {
	const base int64 = 86400
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.H1: confirmedFrame(market.H1, base+6900, structure.TrendBullish),
		market.H4: confirmedFrame(market.H4, base-60000, structure.TrendBearish),
	}}
	got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900)
	if len(got) != 0 {
		t.Fatalf("future H1 close and stale H4 must be omitted, got %+v", got)
	}
}

func TestClosedHigherTimeframeBiases_NeverGuessesUnconfirmedStructure(t *testing.T) {
	const base int64 = 86400
	ctx := &context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.H1: confirmedFrame(market.H1, base+3600, structure.TrendRange),
	}}
	if got := engine.ClosedHigherTimeframeBiases(ctx, market.M5, base+6900); len(got) != 0 {
		t.Fatalf("no confirmed trend must not become guessed HTF bias, got %+v", got)
	}
}
