package supply_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/supply"
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
	s, err := supply.New(strategy.Config{ID: supply.ID, Version: supply.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("supply.New: %v", err)
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

func freshSupplyZone(id string, low, high float64) zone.Zone {
	touchedAt := int64(1000)
	return zone.Zone{
		ID: id, Kind: zone.KindSupply, Side: zone.Supply,
		Low: market.Price(low), High: market.Price(high),
		Layer: structure.StructureInternal, Timeframe: market.M5,
		CreatedAt: 900, LastTouchedAt: &touchedAt, TouchCount: 1,
		Strength: 0.8, State: zone.StateFresh, Relevance: zone.Immediate,
	}
}

func TestSupply_ValidZoneProducesASellCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	sellPool := liquidity.Pool{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Zones:     zone.ZoneState{Zones: []zone.Zone{freshSupplyZone("z1", 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{sellPool}},
	}

	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	c := candidates[0]
	if c.Direction != market.Sell {
		t.Errorf("expected SELL direction, got %v", c.Direction)
	}
	if c.Strategy != supply.ID || c.StrategyVersion != supply.Version {
		t.Errorf("expected strategy=%s version=%s, got %s/%s", supply.ID, supply.Version, c.Strategy, c.StrategyVersion)
	}
	if c.Entry.Low != 2020 || c.Entry.High != 2022 {
		t.Errorf("expected entry to match the zone band, got %+v", c.Entry)
	}
	if float64(c.Invalidation.Price) <= 2022 {
		t.Errorf("expected invalidation beyond the zone's high, got %v", c.Invalidation.Price)
	}
	if err := c.Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestSupply_MitigatedZoneProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := freshSupplyZone("z1", 2020, 2022)
	z.State = zone.StateMitigated
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a Mitigated zone, got %d", len(got))
	}
}

func TestSupply_RemoteZoneProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := freshSupplyZone("z1", 2020, 2022)
	z.Relevance = zone.Remote
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a Remote (not currently relevant) zone, got %d", len(got))
	}
}

func TestSupply_WeakZoneBelowMinimumStrengthProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := freshSupplyZone("z1", 2020, 2022)
	z.Strength = 0.1 // below the configured 0.3 floor
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a below-threshold-strength zone, got %d", len(got))
	}
}

func TestSupply_NoOpposingLiquidityProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{freshSupplyZone("z1", 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{}, // no pools at all
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate without a technical target, got %d", len(got))
	}
}

func TestSupply_MissingPrimaryTimeframeProducesNoCandidate(t *testing.T) {
	// Insufficient history: the strategy's required M5 timeframe has no
	// context yet (a fresh symbol) — this must never panic.
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0) // Timeframes map left empty
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate when the required timeframe is absent, got %d", len(got))
	}
}

func TestSupply_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0) // no volatility reading yet
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{freshSupplyZone("z1", 2020, 2022)}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate with zero ATR (cannot express ATR-relative thresholds), got %d", len(got))
	}
}

// TestSupply_DemandZoneNeverProducesASupplyCandidate proves strategy
// isolation from the OTHER zone kind at the data level: a Kind=Demand
// zone must never be picked up by SupplyStrategy even if every other
// field looks identical.
func TestSupply_DemandZoneNeverProducesASupplyCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	z := freshSupplyZone("z1", 2020, 2022)
	z.Kind = zone.KindDemand
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected SupplyStrategy to ignore a Demand-kind zone entirely, got %d", len(got))
	}
}

// TestSupply_SameSetupIsDeterministicAcrossEvaluations is the causality/
// determinism proof: evaluating the identical MarketContext twice must
// yield the identical opportunity ID — required for the OpportunityBook's
// own dedup to work at all (docs/analysis/opportunity-lifecycle-v2.md).
func TestSupply_SameSetupIsDeterministicAcrossEvaluations(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func() *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{freshSupplyZone("z1", 2020, 2022)}},
			Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
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

// TestSupply_NonXAUInstrumentGeometryStillProducesAValidCandidate proves
// this strategy makes no XAU-scale price assumption — a fine-digit FX
// pair (EURUSD, pip 0.0001) must work identically to XAU (pip 0.1).
func TestSupply_NonXAUInstrumentGeometryStillProducesAValidCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0.0010) // a realistic EURUSD M5 ATR
	ctx.Symbol = "EURUSD"
	z := zone.Zone{
		ID: "z1", Kind: zone.KindSupply, Side: zone.Supply,
		Low: 1.10200, High: 1.10220, Layer: structure.StructureInternal, Timeframe: market.M5,
		CreatedAt: 900, TouchCount: 0, Strength: 0.8, State: zone.StateFresh, Relevance: zone.Immediate,
	}
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5, Zones: zone.ZoneState{Zones: []zone.Zone{z}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 1.10000, High: 1.10010}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate for EURUSD geometry, got %d", len(candidates))
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a valid candidate at FX price scale, got: %v", err)
	}
}

func TestSupply_RejectsAMissingRequiredParameter(t *testing.T) {
	params := validParams()
	delete(params, "invalidation_buffer_atr")
	if _, err := supply.New(strategy.Config{ID: supply.ID, Version: supply.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to fail closed on a missing required parameter")
	}
}

func TestSupply_RequiredTimeframesIsM5(t *testing.T) {
	s := newStrategy(t, validParams())
	tfs := s.RequiredTimeframes()
	if len(tfs) != 1 || tfs[0] != market.M5 {
		t.Errorf("expected RequiredTimeframes()==[M5], got %v", tfs)
	}
}
