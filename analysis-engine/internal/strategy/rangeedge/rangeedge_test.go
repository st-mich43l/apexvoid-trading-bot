package rangeedge

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func testBar(at int64, open, high, low, close float64) market.Candle {
	return market.Candle{Time: at, Open: open, High: high, Low: low, Close: close, Volume: 1}
}

func params() map[string]any {
	return map[string]any{"lookback_bars": 8.0, "minimum_touches": 2.0, "minimum_wick_rejections": 1.0, "break_closes": 2.0, "cluster_atr": .3, "entry_tolerance_atr": .25, "minimum_wick_fraction": .25, "minimum_width_atr": 1.0, "maximum_width_atr": 6.0, "minimum_room_atr": .75, "invalidation_buffer_atr": .25, "expiry_hours": 4.0, "reaction_lookback_bars": 3.0, "engulfing_minimum_range_atr": .5}
}

func rangeBars() []market.Candle {
	return []market.Candle{
		testBar(1, 100.4, 102, 99.5, 101.5),
		testBar(2, 101.5, 103, 101, 102),
		testBar(3, 103.2, 104.1, 102, 102.8),
		testBar(4, 102.5, 103.2, 101.3, 102),
		testBar(5, 100.5, 102.2, 99.55, 101.5),
		testBar(6, 101.5, 103, 101, 102.2),
		testBar(7, 103.1, 104.05, 102, 102.7),
		testBar(8, 100.4, 101.4, 99.4, 101.2),
	}
}

func TestLegacyRangeBarrierTouchHistoryAndTargets(t *testing.T) {
	s, err := New(strategy.Config{ID: ID, Version: Version, Parameters: params()})
	if err != nil {
		t.Fatal(err)
	}
	bars := rangeBars()
	ctx := &analysiscontext.MarketContext{Symbol: "XAU", Volatility: analysiscontext.VolatilityContext{ATR: 1}, Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: bars}}}
	got := s.Evaluate(ctx)
	if len(got) != 1 {
		t.Fatalf("established lower barrier reaction should qualify: %+v", got)
	}
	if got[0].Reaction == nil || len(got[0].Targets) != 2 || got[0].Targets[0].Price.Label != "range_equilibrium" || got[0].Targets[1].Price.Label != "opposite_range_edge" {
		t.Fatalf("range provenance/targets missing: %+v", got[0])
	}
}

func TestLegacyRangeAcceptedCloseInvalidatesBarrier(t *testing.T) {
	s, err := New(strategy.Config{ID: ID, Version: Version, Parameters: params()})
	if err != nil {
		t.Fatal(err)
	}
	bars := rangeBars()
	bars[6] = testBar(7, 99.4, 100, 98.8, 99.0)
	bars[7] = testBar(8, 99.0, 99.5, 98.5, 98.9)
	ctx := &analysiscontext.MarketContext{Symbol: "XAU", Volatility: analysiscontext.VolatilityContext{ATR: 1}, Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: bars}}}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatalf("two accepted closes through the edge must reject: %+v", got)
	}
}
