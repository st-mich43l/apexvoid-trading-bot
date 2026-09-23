package keylevel_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	strategykeylevel "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func validParams() map[string]any {
	return map[string]any{
		"minimum_touches":             2.0,
		"minimum_strength":            0.3,
		"proximity_atr":               1.0,
		"invalidation_buffer_atr":     0.5,
		"minimum_target_distance_atr": 1.0,
		"expiry_hours":                24.0,
	}
}

func newStrategy(t *testing.T, params map[string]any) strategy.Strategy {
	t.Helper()
	s, err := strategykeylevel.New(strategy.Config{ID: strategykeylevel.ID, Version: strategykeylevel.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("keylevel.New: %v", err)
	}
	return s
}

func baseContext(atr float64) *context.MarketContext {
	return &context.MarketContext{
		Symbol:     "XAU",
		Timeframes: map[market.Timeframe]*context.TimeframeContext{},
		Volatility: context.VolatilityContext{ATR: atr},
	}
}

// structStateWithPrice fabricates a StructureState whose Micro.LastHigh
// gives currentPriceProxy() a known, deterministic "current price".
func structStateWithPrice(price float64, t int64) structure.StructureState {
	swing := &structure.Swing{ID: "s1", Kind: structure.SwingHigh, Layer: structure.StructureMicro, Time: t, Price: market.Price(price)}
	return structure.StructureState{
		Micro: structure.LayerState{Layer: structure.StructureMicro, LastHigh: swing},
	}
}

func TestKeyLevel_PriceAboveLevelProducesABuyCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2021.0, 1000), // current price above the level -> support -> BUY
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{{Price: 2020, Kind: keylevel.KindReaction, Touches: 3, Band: 0.5, Strength: 0.8}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Buy {
		t.Errorf("expected BUY direction when price sits above the level (support), got %v", candidates[0].Direction)
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestKeyLevel_PriceBelowLevelProducesASellCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2019.0, 1000), // current price below the level -> resistance -> SELL
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{{Price: 2020, Kind: keylevel.KindReaction, Touches: 3, Band: 0.5, Strength: 0.8}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Sell {
		t.Errorf("expected SELL direction when price sits below the level (resistance), got %v", candidates[0].Direction)
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestKeyLevel_InsufficientTouchesProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2021.0, 1000),
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{{Price: 2020, Kind: keylevel.KindReaction, Touches: 1, Band: 0.5, Strength: 0.8}}}, // below configured minimum_touches=2
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a level with insufficient touches, got %d", len(got))
	}
}

func TestKeyLevel_TooFarFromCurrentPriceProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2050.0, 1000), // 30 ATR away from the level, proximity_atr=1.0
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{{Price: 2020, Kind: keylevel.KindReaction, Touches: 3, Band: 0.5, Strength: 0.8}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a level too far from current price, got %d", len(got))
	}
}

func TestKeyLevel_WeakLevelProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2021.0, 1000),
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{{Price: 2020, Kind: keylevel.KindReaction, Touches: 3, Band: 0.5, Strength: 0.1}}}, // below minimum_strength=0.3
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a below-threshold-strength level, got %d", len(got))
	}
}

// TestKeyLevel_NoStructureSwingsYetProducesNoCandidate is the
// "insufficient history" case: a fresh symbol has no Micro swing yet, so
// currentPriceProxy has nothing to derive a price from.
func TestKeyLevel_NoStructureSwingsYetProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structure.StructureState{}, // no Micro.LastHigh/LastLow at all
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{{Price: 2020, Kind: keylevel.KindReaction, Touches: 3, Band: 0.5, Strength: 0.8}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate without any Micro-layer swing to derive a price proxy from, got %d", len(got))
	}
}

func TestKeyLevel_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2021.0, 1000),
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{{Price: 2020, Kind: keylevel.KindReaction, Touches: 3, Band: 0.5, Strength: 0.8}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate with zero ATR, got %d", len(got))
	}
}

func TestKeyLevel_SameSetupIsDeterministicAcrossEvaluations(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func() *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5,
			Structure: structStateWithPrice(2021.0, 1000),
			KeyLevel:  keylevel.State{Levels: []keylevel.Level{{Price: 2020, Kind: keylevel.KindReaction, Touches: 3, Band: 0.5, Strength: 0.8}}},
			Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
		}
		return ctx
	}
	first := s.Evaluate(build())
	second := s.Evaluate(build())
	if len(first) != 1 || len(second) != 1 {
		t.Fatalf("expected exactly 1 candidate each evaluation, got %d and %d", len(first), len(second))
	}
	if first[0].ID != second[0].ID {
		t.Errorf("expected a deterministic opportunity ID across identical evaluations, got %q vs %q", first[0].ID, second[0].ID)
	}
}

func TestKeyLevel_RejectsAMissingRequiredParameter(t *testing.T) {
	params := validParams()
	delete(params, "proximity_atr")
	if _, err := strategykeylevel.New(strategy.Config{ID: strategykeylevel.ID, Version: strategykeylevel.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to fail closed on a missing required parameter")
	}
}

func TestKeyLevel_RejectsZeroMinimumTouches(t *testing.T) {
	params := validParams()
	params["minimum_touches"] = 0.0
	if _, err := strategykeylevel.New(strategy.Config{ID: strategykeylevel.ID, Version: strategykeylevel.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to reject minimum_touches < 1")
	}
}

func TestKeyLevel_RequiredTimeframesIsM5(t *testing.T) {
	s := newStrategy(t, validParams())
	tfs := s.RequiredTimeframes()
	if len(tfs) != 1 || tfs[0] != market.M5 {
		t.Errorf("expected RequiredTimeframes()==[M5], got %v", tfs)
	}
}
