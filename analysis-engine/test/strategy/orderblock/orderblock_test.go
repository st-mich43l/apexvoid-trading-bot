package orderblock_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/orderblock"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func validParams() map[string]any {
	return map[string]any{
		"minimum_strength":            0.3,
		"invalidation_buffer_atr":     0.5,
		"minimum_target_distance_atr": 1.0,
		"expiry_hours":                24.0,
	}
}

func newStrategy(t *testing.T, params map[string]any) strategy.Strategy {
	t.Helper()
	s, err := orderblock.New(strategy.Config{ID: orderblock.ID, Version: orderblock.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("orderblock.New: %v", err)
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

func obZone(id string, side zone.Side, low, high float64) zone.Zone {
	touchedAt := int64(1000)
	return zone.Zone{
		ID: id, Kind: zone.KindOrderBlock, Side: side,
		Low: market.Price(low), High: market.Price(high),
		Layer: structure.StructureInternal, Timeframe: market.M5,
		CreatedAt: 900, LastTouchedAt: &touchedAt, TouchCount: 1,
		Strength: 0.8, State: zone.StateFresh, Relevance: zone.Immediate,
	}
}

func TestOrderBlock_DemandSideProducesABuyCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Zones:     zone.ZoneState{Zones: []zone.Zone{obZone("z1", zone.Demand, 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	c := candidates[0]
	if c.Direction != market.Buy {
		t.Errorf("expected BUY direction for a Demand-side order block, got %v", c.Direction)
	}
	if float64(c.Invalidation.Price) >= 2020 {
		t.Errorf("expected invalidation below the block's low, got %v", c.Invalidation.Price)
	}
	if err := c.Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestOrderBlock_SupplySideProducesASellCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Zones:     zone.ZoneState{Zones: []zone.Zone{obZone("z1", zone.Supply, 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	c := candidates[0]
	if c.Direction != market.Sell {
		t.Errorf("expected SELL direction for a Supply-side order block, got %v", c.Direction)
	}
	if float64(c.Invalidation.Price) <= 2022 {
		t.Errorf("expected invalidation above the block's high, got %v", c.Invalidation.Price)
	}
	if err := c.Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestOrderBlock_MitigatedZoneProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := obZone("z1", zone.Demand, 2020, 2022)
	z.State = zone.StateMitigated
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a Mitigated order block, got %d", len(got))
	}
}

func TestOrderBlock_RemoteZoneProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := obZone("z1", zone.Demand, 2020, 2022)
	z.Relevance = zone.Remote
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a Remote order block, got %d", len(got))
	}
}

func TestOrderBlock_WeakZoneBelowMinimumStrengthProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := obZone("z1", zone.Demand, 2020, 2022)
	z.Strength = 0.1
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a below-threshold-strength order block, got %d", len(got))
	}
}

func TestOrderBlock_NonOrderBlockKindProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := obZone("z1", zone.Demand, 2020, 2022)
	z.Kind = zone.KindBreaker
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected OrderBlockStrategy to ignore a Breaker-kind zone entirely, got %d", len(got))
	}
}

func TestOrderBlock_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{obZone("z1", zone.Demand, 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate with zero ATR, got %d", len(got))
	}
}

func TestOrderBlock_SameSetupIsDeterministicAcrossEvaluations(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func() *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{obZone("z1", zone.Demand, 2020, 2022)}},
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

func TestOrderBlock_RejectsAMissingRequiredParameter(t *testing.T) {
	params := validParams()
	delete(params, "minimum_target_distance_atr")
	if _, err := orderblock.New(strategy.Config{ID: orderblock.ID, Version: orderblock.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to fail closed on a missing required parameter")
	}
}

func TestOrderBlock_RequiredTimeframesIsM5(t *testing.T) {
	s := newStrategy(t, validParams())
	tfs := s.RequiredTimeframes()
	if len(tfs) != 1 || tfs[0] != market.M5 {
		t.Errorf("expected RequiredTimeframes()==[M5], got %v", tfs)
	}
}
