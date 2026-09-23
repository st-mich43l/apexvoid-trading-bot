package flipzone_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/flipzone"
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
	s, err := flipzone.New(strategy.Config{ID: flipzone.ID, Version: flipzone.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("flipzone.New: %v", err)
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

func flip(id string, side zone.Side, low, high float64, touchCount int) zone.Zone {
	touchedAt := int64(1000)
	z := zone.Zone{
		ID: id, Kind: zone.KindFlip, Side: side,
		Low: market.Price(low), High: market.Price(high),
		Layer: structure.StructureInternal, Timeframe: market.M5,
		CreatedAt: 900, TouchCount: touchCount,
		Strength: 0.8, State: zone.StateFresh, Relevance: zone.Immediate,
	}
	if touchCount > 0 {
		z.LastTouchedAt = &touchedAt
	}
	return z
}

func TestFlipZone_DemandSideProducesABuyCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Zones:     zone.ZoneState{Zones: []zone.Zone{flip("z1", zone.Demand, 2020, 2022, 1)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Buy {
		t.Errorf("expected BUY direction for a Demand-side flip (new role after break), got %v", candidates[0].Direction)
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestFlipZone_SupplySideProducesASellCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Zones:     zone.ZoneState{Zones: []zone.Zone{flip("z1", zone.Supply, 2020, 2022, 1)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Sell {
		t.Errorf("expected SELL direction for a Supply-side flip, got %v", candidates[0].Direction)
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

// TestFlipZone_AConfirmedRetestScoresHigherThanAnUnconfirmedFlip proves
// FlipZone's own retest-quality reasoning: 1-2 touches (a real retest)
// beats zero touches (unconfirmed), which is the OPPOSITE polarity from
// FVG's fill-erosion reasoning — each strategy reasons independently
// about the same "touch count" raw fact (source task §40).
func TestFlipZone_AConfirmedRetestScoresHigherThanAnUnconfirmedFlip(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func(touchCount int) *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5,
			Zones:     zone.ZoneState{Zones: []zone.Zone{flip("z1", zone.Demand, 2020, 2022, touchCount)}},
			Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
		}
		return ctx
	}
	unconfirmed := s.Evaluate(build(0))
	confirmed := s.Evaluate(build(1))
	wornDown := s.Evaluate(build(4))
	if len(unconfirmed) != 1 || len(confirmed) != 1 || len(wornDown) != 1 {
		t.Fatalf("expected 1 candidate each, got %d, %d, %d", len(unconfirmed), len(confirmed), len(wornDown))
	}
	if confirmed[0].Quality.Overall <= unconfirmed[0].Quality.Overall {
		t.Errorf("expected a confirmed retest to score higher than an unconfirmed flip: unconfirmed=%v confirmed=%v",
			unconfirmed[0].Quality.Overall, confirmed[0].Quality.Overall)
	}
	if confirmed[0].Quality.Overall <= wornDown[0].Quality.Overall {
		t.Errorf("expected a confirmed (1-touch) retest to score higher than an over-tested (worn down) flip: confirmed=%v wornDown=%v",
			confirmed[0].Quality.Overall, wornDown[0].Quality.Overall)
	}
}

func TestFlipZone_InvalidatedZoneProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := flip("z1", zone.Demand, 2020, 2022, 1)
	z.State = zone.StateInvalidated
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for an Invalidated flip, got %d", len(got))
	}
}

func TestFlipZone_NonFlipKindProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := flip("z1", zone.Demand, 2020, 2022, 1)
	z.Kind = zone.KindOrderBlock
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected FlipZoneStrategy to ignore an OrderBlock-kind zone entirely, got %d", len(got))
	}
}

func TestFlipZone_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{flip("z1", zone.Demand, 2020, 2022, 1)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate with zero ATR, got %d", len(got))
	}
}

func TestFlipZone_SameSetupIsDeterministicAcrossEvaluations(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func() *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{flip("z1", zone.Demand, 2020, 2022, 1)}},
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

func TestFlipZone_RejectsAMissingRequiredParameter(t *testing.T) {
	params := validParams()
	delete(params, "invalidation_buffer_atr")
	if _, err := flipzone.New(strategy.Config{ID: flipzone.ID, Version: flipzone.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to fail closed on a missing required parameter")
	}
}

func TestFlipZone_RequiredTimeframesIsM5(t *testing.T) {
	s := newStrategy(t, validParams())
	tfs := s.RequiredTimeframes()
	if len(tfs) != 1 || tfs[0] != market.M5 {
		t.Errorf("expected RequiredTimeframes()==[M5], got %v", tfs)
	}
}
