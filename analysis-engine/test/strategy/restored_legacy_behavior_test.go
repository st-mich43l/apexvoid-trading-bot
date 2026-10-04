package strategy_test

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/fadescalp"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/momentumride"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/snapback"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func restoredContext(direction market.Direction, candles []market.Candle) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU", Bias: analysiscontext.BiasContext{Direction: direction}, Volatility: analysiscontext.VolatilityContext{ATR: 1},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: candles, Regime: regime.State{Kind: "trend"}}},
	}
}

func TestRestoredSnapBackRequiresLocationGrabAndReaction(t *testing.T) {
	s, err := snapback.New(strategy.Config{ID: snapback.ID, Version: "v2", Parameters: snapBackParams()})
	if err != nil {
		t.Fatal(err)
	}
	candles := []market.Candle{{Time: 1000, Open: 99.2, High: 100.8, Low: 98.5, Close: 100.5}}
	ctx := restoredContext(market.Buy, candles)
	tf := ctx.Timeframes[market.M5]
	tf.Structure.Swings = []structure.Swing{{ID: "low", Kind: structure.SwingLow, Price: 96, Time: 700}}
	tf.Zones.Zones = []zone.Zone{{ID: "demand", Side: zone.Demand, Kind: zone.KindDemand, Low: 99, High: 100, OriginTime: 600, State: zone.StateFresh, Strength: .8, TouchCount: 2}}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatal("snap back must reject without an A/B grab")
	}
	swept, reclaimed := int64(1000), int64(1000)
	tf.Liquidity.Pools = []liquidity.Pool{{ID: "eq-low", Side: liquidity.LiquiditySellSide, Source: "equal_low", Low: 99.1, High: 99.2, SweptAt: &swept, ReclaimedAt: &reclaimed, CreatedAt: 500, TouchCount: 3}}
	got := s.Evaluate(ctx)
	if len(got) != 1 || got[0].StructuralID != "snap:demand" || got[0].Reaction == nil {
		t.Fatalf("qualified legacy snap back missing: %+v", got)
	}
	tf.Fib.Range = &fib.DealingRange{Zone: "premium"}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatal("BUY snap back must reject premium location")
	}
}

func TestRestoredFadeEqualLevelAndChopRules(t *testing.T) {
	params := map[string]any{"proximal_band_atr": .5, "invalidation_buffer_atr": .25, "target_r": 2.0, "expiry_hours": 4.0, "chop_edge_fraction": .25, "strict_premium_discount": true, "reaction_lookback_bars": 3.0, "engulfing_minimum_range_atr": .5}
	s, err := fadescalp.New(strategy.Config{ID: fadescalp.ID, Version: "v2", Parameters: params})
	if err != nil {
		t.Fatal(err)
	}
	swept, reclaimed := int64(1000), int64(1000)
	ctx := restoredContext(market.Buy, []market.Candle{{Time: 1000, Open: 99.2, High: 100.8, Low: 98.5, Close: 100.5}})
	tf := ctx.Timeframes[market.M5]
	tf.Liquidity.Pools = []liquidity.Pool{
		{ID: "swing", Side: liquidity.LiquiditySellSide, Source: "swing_low", Low: 99.1, High: 99.2, SweptAt: &swept, ReclaimedAt: &reclaimed, CreatedAt: 500},
		{ID: "equal", Side: liquidity.LiquiditySellSide, Source: "equal_low", Low: 99.1, High: 99.2, SweptAt: &swept, ReclaimedAt: &reclaimed, CreatedAt: 500, TouchCount: 3},
		{ID: "target", Side: liquidity.LiquidityBuySide, Low: 103, High: 103.2},
	}
	if rc := strategyutil.ConfirmReaction(tf, "equal", market.Buy, 98.65, 99.65, 1, 500, strategyutil.ReactionConfig{LookbackBars: 3, EngulfingMinimumRangeATR: .5}); rc == nil {
		t.Fatal("fixture must contain a structural reaction")
	}
	got := s.Evaluate(ctx)
	if len(got) != 1 || got[0].StructuralID != "fade:equal" {
		t.Fatalf("equal-low BUY fade missing: %+v", got)
	}
	tf.Regime = regime.State{Kind: "chop", RangeLow: 90, RangeHigh: 100}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatal("chop fade away from the strict range edge must reject")
	}
	// A clean same-bar reclaim with a sub-Grade-A body is B even when the
	// persisted lifecycle timestamp is changed. The detector must inspect the
	// actual candle, not blindly trust ReclaimedAt.
	tf.Candles[0].Open = 99.9
	tf.Candles[0].Close = 100.1
	tf.Regime = regime.State{Kind: "chop", RangeLow: 99, RangeHigh: 110}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatal("chop fade requires Grade A, not B")
	}
}

func TestRestoredMomentumUsesStructuralBreakAndZone(t *testing.T) {
	s, err := momentumride.New(strategy.Config{ID: momentumride.ID, Version: "v2", Parameters: momentumRideParams()})
	if err != nil {
		t.Fatal(err)
	}
	ctx := restoredContext(market.Buy, []market.Candle{{Time: 900, Open: 99, High: 100, Low: 98.8, Close: 99.8}, {Time: 1000, Open: 100, High: 102, Low: 99.9, Close: 101.8}})
	tf := ctx.Timeframes[market.M5]
	tf.Momentum = momentum.Result{State: momentum.Bull, Velocity: .3, Acceleration: .01}
	tf.Structure.Swings = []structure.Swing{{ID: "broken-high", Kind: structure.SwingHigh, Price: 100, Time: 800}}
	tf.Zones.Zones = []zone.Zone{{ID: "demand", Side: zone.Demand, Low: 100, High: 100.5, State: zone.StateFresh, Strength: .8}}
	tf.Liquidity.Pools = []liquidity.Pool{{ID: "target", Side: liquidity.LiquidityBuySide, Low: 104, High: 104.2}}
	got := s.Evaluate(ctx)
	if len(got) != 1 || got[0].StructuralID != "momentum:broken-high:demand" {
		t.Fatalf("structural momentum candidate missing: %+v", got)
	}
	tf.Regime.Kind = "chop"
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatal("momentum ride must reject chop")
	}
	tf.Regime.Kind = "trend"
	tf.Structure.Swings = nil
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatal("three-candle continuity cannot replace a broken structural swing")
	}
}

func TestLegacyParityScoreWinsAnchorSelection(t *testing.T) {
	ctx := restoredContext(market.Buy, []market.Candle{{Time: 1000, Open: 99, High: 101, Low: 98, Close: 100}})
	ctx.Timeframes[market.M5].Zones.Zones = []zone.Zone{
		{ID: "go-strong", Side: zone.Demand, Low: 99, High: 100, State: zone.StateFresh, Strength: 99, LegacyScore: 1},
		{ID: "python-strong", Side: zone.Demand, Low: 99.2, High: 100.2, State: zone.StateFresh, Strength: .1, LegacyScore: 5},
	}
	zones := strategyutil.LiveZones(ctx.Timeframes[market.M5], market.Buy, 100, 1, 2)
	if len(zones) != 2 || zones[0].ID != "python-strong" {
		t.Fatalf("legacy source score must select the Python strong anchor first: %+v", zones)
	}
}
