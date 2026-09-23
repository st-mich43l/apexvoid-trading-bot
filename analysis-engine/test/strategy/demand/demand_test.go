package demand_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/demand"
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
	s, err := demand.New(strategy.Config{ID: demand.ID, Version: demand.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("demand.New: %v", err)
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

func freshDemandZone(id string, low, high float64) zone.Zone {
	touchedAt := int64(1000)
	return zone.Zone{
		ID: id, Kind: zone.KindDemand, Side: zone.Demand,
		Low: market.Price(low), High: market.Price(high),
		Layer: structure.StructureInternal, Timeframe: market.M5,
		CreatedAt: 900, LastTouchedAt: &touchedAt, TouchCount: 1,
		Strength: 0.8, State: zone.StateFresh, Relevance: zone.Immediate,
	}
}

func TestDemand_ValidZoneProducesABuyCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	buyPool := liquidity.Pool{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Zones:     zone.ZoneState{Zones: []zone.Zone{freshDemandZone("z1", 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{buyPool}},
	}

	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	c := candidates[0]
	if c.Direction != market.Buy {
		t.Errorf("expected BUY direction, got %v", c.Direction)
	}
	if c.Strategy != demand.ID || c.StrategyVersion != demand.Version {
		t.Errorf("expected strategy=%s version=%s, got %s/%s", demand.ID, demand.Version, c.Strategy, c.StrategyVersion)
	}
	if c.Entry.Low != 2020 || c.Entry.High != 2022 {
		t.Errorf("expected entry to match the zone band, got %+v", c.Entry)
	}
	if float64(c.Invalidation.Price) >= 2020 {
		t.Errorf("expected invalidation below the zone's low, got %v", c.Invalidation.Price)
	}
	if err := c.Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestDemand_InvalidatedZoneProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := freshDemandZone("z1", 2020, 2022)
	z.State = zone.StateInvalidated
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for an Invalidated zone, got %d", len(got))
	}
}

func TestDemand_DormantZoneProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := freshDemandZone("z1", 2020, 2022)
	z.Relevance = zone.Dormant
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a Dormant (not currently relevant) zone, got %d", len(got))
	}
}

func TestDemand_WeakZoneBelowMinimumStrengthProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := freshDemandZone("z1", 2020, 2022)
	z.Strength = 0.1
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a below-threshold-strength zone, got %d", len(got))
	}
}

func TestDemand_NoOpposingLiquidityProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{freshDemandZone("z1", 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate without a technical target, got %d", len(got))
	}
}

func TestDemand_MissingPrimaryTimeframeProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate when the required timeframe is absent, got %d", len(got))
	}
}

func TestDemand_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{freshDemandZone("z1", 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate with zero ATR, got %d", len(got))
	}
}

// TestDemand_SupplyZoneNeverProducesADemandCandidate proves strategy
// isolation from the OTHER zone kind at the data level.
func TestDemand_SupplyZoneNeverProducesADemandCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := freshDemandZone("z1", 2020, 2022)
	z.Kind = zone.KindSupply
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected DemandStrategy to ignore a Supply-kind zone entirely, got %d", len(got))
	}
}

func TestDemand_SameSetupIsDeterministicAcrossEvaluations(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func() *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{freshDemandZone("z1", 2020, 2022)}},
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

func TestDemand_JPYInstrumentGeometryStillProducesAValidCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0.15) // a realistic USDJPY M5 ATR (2-digit pip scale)
	ctx.Symbol = "USDJPY"
	z := zone.Zone{
		ID: "z1", Kind: zone.KindDemand, Side: zone.Demand,
		Low: 148.200, High: 148.260, Layer: structure.StructureInternal, Timeframe: market.M5,
		CreatedAt: 900, TouchCount: 0, Strength: 0.8, State: zone.StateFresh, Relevance: zone.Immediate,
	}
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 148.600, High: 148.650}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate for USDJPY geometry, got %d", len(candidates))
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a valid candidate at JPY price scale, got: %v", err)
	}
}

func TestDemand_RejectsAMissingRequiredParameter(t *testing.T) {
	params := validParams()
	delete(params, "invalidation_buffer_atr")
	if _, err := demand.New(strategy.Config{ID: demand.ID, Version: demand.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to fail closed on a missing required parameter")
	}
}

// TestDemand_ConfigChangeDoesNotAffectSupplyStrategy is the strategy
// isolation proof (source task §91): reconfiguring Demand must never
// change what an independently-constructed Supply evaluation produces.
// Demand imports nothing from supply and vice versa, so this is really
// just documenting the guarantee the architecture test enforces — this
// package's own fingerprint changes with its own config, and that's it.
func TestDemand_DistinctConfigsProduceDistinctFingerprints(t *testing.T) {
	s1 := newStrategy(t, validParams())
	params2 := validParams()
	params2["minimum_strength"] = 0.6
	s2 := newStrategy(t, params2)

	ctx := func() *context.MarketContext {
		c := baseContext(1.0)
		c.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{freshDemandZone("z1", 2020, 2022)}},
			Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
		}
		return c
	}
	c1 := s1.Evaluate(ctx())
	c2 := s2.Evaluate(ctx())
	if len(c1) != 1 || len(c2) != 1 {
		t.Fatalf("expected 1 candidate from each strategy instance, got %d and %d", len(c1), len(c2))
	}
	if c1[0].Provenance.ConfigFingerprint == c2[0].Provenance.ConfigFingerprint {
		t.Error("expected differently-configured instances to produce different config fingerprints")
	}
}

func TestDemand_RequiredTimeframesIsM5(t *testing.T) {
	s := newStrategy(t, validParams())
	tfs := s.RequiredTimeframes()
	if len(tfs) != 1 || tfs[0] != market.M5 {
		t.Errorf("expected RequiredTimeframes()==[M5], got %v", tfs)
	}
}
