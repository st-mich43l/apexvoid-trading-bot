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
	return map[string]any{"lookback_bars": 8.0, "minimum_touches": 2.0, "minimum_wick_rejections": 1.0, "minimum_inside_closes": 3.0, "inside_lookback_bars": 24.0, "recent_breakout_lookback_bars": 12.0, "fallback_minimum_confirmations": 1.0, "fallback_enabled": true, "provisional_enabled": true, "post_impulse_enabled": true, "fallback_min_width_atr": .8, "fallback_max_width_atr": 8.0, "fallback_wick_fraction": .25, "post_impulse_min_displacement_atr": 3.0, "post_impulse_max_contraction_atr": 2.2, "post_impulse_min_inside_closes": 4.0, "post_impulse_lookback_bars": 36.0, "post_impulse_recent_bars": 6.0, "cluster_atr": .3, "cluster_min_abs": 0.0, "cluster_pip_mult": 2.0, "entry_tolerance_atr": .25, "maximum_edge_width_atr": .75, "minimum_wick_fraction": .25, "minimum_width_atr": 1.0, "maximum_width_atr": 6.0, "minimum_room_atr": .75, "invalidation_buffer_atr": .25, "recent_breakout_buffer_atr": .15, "recent_breakout_min_span_atr": .8, "expiry_hours": 4.0, "reaction_lookback_bars": 3.0, "engulfing_minimum_range_atr": .5}
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
	created, err := New(strategy.Config{ID: ID, Version: Version, Parameters: params()})
	if err != nil {
		t.Fatal(err)
	}
	s := created.(*Strategy)
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

func TestRangeEdgeEntryMustBeNearTheSelectedEdge(t *testing.T) {
	if !entryWithinDistance(100.5, 100, 101, market.Buy, 1, 2) {
		t.Fatal("price inside a buy edge should be valid")
	}
	if !entryWithinDistance(102.5, 100, 101, market.Buy, 1, 2) {
		t.Fatal("buy price within the maximum ATR distance should be valid")
	}
	if entryWithinDistance(104, 100, 101, market.Buy, 1, 2) {
		t.Fatal("stale buy edge must be rejected")
	}
	if !entryWithinDistance(99.5, 100, 101, market.Sell, 1, 2) {
		t.Fatal("sell price within the maximum ATR distance should be valid")
	}
	if entryWithinDistance(97, 100, 101, market.Sell, 1, 2) {
		t.Fatal("stale sell edge must be rejected")
	}
}

func TestRangeStateGates(t *testing.T) {
	created, err := New(strategy.Config{ID: ID, Version: Version, Parameters: params()})
	if err != nil {
		t.Fatal(err)
	}
	s := created.(*Strategy)
	lower := &barrier{side: "support", level: 99, wicks: 2, score: 5, executable: true}
	upper := &barrier{side: "resistance", level: 101, wicks: 2, score: 5, executable: true}
	bars := []market.Candle{
		testBar(1, 99.5, 100, 99, 99.6), testBar(2, 99.6, 100, 99.1, 99.7),
		testBar(3, 100, 101, 99.5, 100.4), testBar(4, 100.4, 101, 100, 100.3),
	}
	if got := stateForRange(lower, upper, bars, 1, s); got != "confirmed_range" {
		t.Fatalf("expected confirmed range, got %s", got)
	}
	lower.accepted = 2
	if got := stateForRange(lower, upper, bars, 1, s); got != "broken_range" {
		t.Fatalf("accepted edge should be broken, got %s", got)
	}
}
