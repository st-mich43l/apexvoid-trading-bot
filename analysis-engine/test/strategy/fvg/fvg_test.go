package fvg_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/fvg"
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
	s, err := fvg.New(strategy.Config{ID: fvg.ID, Version: fvg.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("fvg.New: %v", err)
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

func gapZone(id string, side zone.Side, low, high float64, touchCount int) zone.Zone {
	touchedAt := int64(1000)
	return zone.Zone{
		ID: id, Kind: zone.KindFVG, Side: side,
		Low: market.Price(low), High: market.Price(high),
		Layer: structure.StructureInternal, Timeframe: market.M5,
		CreatedAt: 900, LastTouchedAt: &touchedAt, TouchCount: touchCount,
		Strength: 0.8, State: zone.StateFresh, Relevance: zone.Immediate,
	}
}

func TestFVG_DemandSideProducesABuyCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Zones:     zone.ZoneState{Zones: []zone.Zone{gapZone("z1", zone.Demand, 2020, 2022, 0)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Buy {
		t.Errorf("expected BUY direction for a Demand-side FVG, got %v", candidates[0].Direction)
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestFVG_SupplySideProducesASellCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Zones:     zone.ZoneState{Zones: []zone.Zone{gapZone("z1", zone.Supply, 2020, 2022, 0)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Sell {
		t.Errorf("expected SELL direction for a Supply-side FVG, got %v", candidates[0].Direction)
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestFVG_MoreFillsLowersQualityRatherThanRaisingIt(t *testing.T) {
	// FVG's own dimension: touches mean the gap is being consumed, the
	// OPPOSITE of Supply/Demand/OrderBlock's freshness reasoning.
	s := newStrategy(t, validParams())
	build := func(touchCount int) *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5,
			Zones:     zone.ZoneState{Zones: []zone.Zone{gapZone("z1", zone.Demand, 2020, 2022, touchCount)}},
			Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
		}
		return ctx
	}
	fresh := s.Evaluate(build(0))
	partiallyFilled := s.Evaluate(build(2))
	if len(fresh) != 1 || len(partiallyFilled) != 1 {
		t.Fatalf("expected 1 candidate each, got %d and %d", len(fresh), len(partiallyFilled))
	}
	if partiallyFilled[0].Quality.Overall >= fresh[0].Quality.Overall {
		t.Errorf("expected a more-touched (more-filled) gap to score lower quality: fresh=%v partiallyFilled=%v",
			fresh[0].Quality.Overall, partiallyFilled[0].Quality.Overall)
	}
}

func TestFVG_MitigatedZoneProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := gapZone("z1", zone.Demand, 2020, 2022, 0)
	z.State = zone.StateMitigated
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a Mitigated FVG, got %d", len(got))
	}
}

func TestFVG_NonFVGKindProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := gapZone("z1", zone.Demand, 2020, 2022, 0)
	z.Kind = zone.KindIFVG
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected FVGStrategy to ignore an IFVG-kind zone entirely, got %d", len(got))
	}
}

func TestFVG_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{gapZone("z1", zone.Demand, 2020, 2022, 0)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate with zero ATR, got %d", len(got))
	}
}

func TestFVG_SameSetupIsDeterministicAcrossEvaluations(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func() *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{gapZone("z1", zone.Demand, 2020, 2022, 0)}},
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

func TestFVG_RejectsAMissingRequiredParameter(t *testing.T) {
	params := validParams()
	delete(params, "expiry_hours")
	if _, err := fvg.New(strategy.Config{ID: fvg.ID, Version: fvg.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to fail closed on a missing required parameter")
	}
}

func TestFVG_RequiredTimeframesIsM5(t *testing.T) {
	s := newStrategy(t, validParams())
	tfs := s.RequiredTimeframes()
	if len(tfs) != 1 || tfs[0] != market.M5 {
		t.Errorf("expected RequiredTimeframes()==[M5], got %v", tfs)
	}
}
