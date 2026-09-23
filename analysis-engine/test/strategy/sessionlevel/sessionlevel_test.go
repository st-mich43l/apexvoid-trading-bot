package sessionlevel_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func validParams() map[string]any {
	return map[string]any{
		"proximity_atr":               1.0,
		"invalidation_buffer_atr":     0.5,
		"minimum_target_distance_atr": 1.0,
		"expiry_hours":                24.0,
	}
}

func newStrategy(t *testing.T, params map[string]any) strategy.Strategy {
	t.Helper()
	s, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Version: sessionlevel.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("sessionlevel.New: %v", err)
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

func structStateWithPrice(price float64, t int64) structure.StructureState {
	swing := &structure.Swing{ID: "s1", Kind: structure.SwingHigh, Layer: structure.StructureMicro, Time: t, Price: market.Price(price)}
	return structure.StructureState{
		Micro: structure.LayerState{Layer: structure.StructureMicro, LastHigh: swing},
	}
}

func TestSessionLevel_UnsweptHighLevelProducesASellCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2019.5, 1000),
		Session:   session.State{Levels: []session.Level{{Name: "ASIA_H", Price: 2020, Time: 500, Swept: false}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Sell {
		t.Errorf("expected SELL direction for an unswept HIGH-suffixed session level, got %v", candidates[0].Direction)
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestSessionLevel_UnsweptLowLevelProducesABuyCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2020.5, 1000),
		Session:   session.State{Levels: []session.Level{{Name: "PDL", Price: 2020, Time: 500, Swept: false}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Buy {
		t.Errorf("expected BUY direction for an unswept LOW-suffixed session level (PDL), got %v", candidates[0].Direction)
	}
	if err := candidates[0].Validate(); err != nil {
		t.Errorf("expected a fully valid Candidate, got: %v", err)
	}
}

func TestSessionLevel_SweptLevelProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	sweptAt := int64(700)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2019.5, 1000),
		Session:   session.State{Levels: []session.Level{{Name: "ASIA_H", Price: 2020, Time: 500, Swept: true, SweptAt: &sweptAt}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for an already-swept session level, got %d", len(got))
	}
}

func TestSessionLevel_UnrecognizedLevelNameProducesNoCandidate(t *testing.T) {
	// Fail-closed: a name this strategy doesn't recognize must never be
	// guessed at.
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2019.5, 1000),
		Session:   session.State{Levels: []session.Level{{Name: "MYSTERY", Price: 2020, Time: 500, Swept: false}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for an unrecognized level name, got %d", len(got))
	}
}

func TestSessionLevel_TooFarFromCurrentPriceProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(1990.0, 1000), // 30 ATR away, proximity_atr=1.0
		Session:   session.State{Levels: []session.Level{{Name: "ASIA_H", Price: 2020, Time: 500, Swept: false}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a level too far from current price, got %d", len(got))
	}
}

func TestSessionLevel_NoStructureSwingsYetProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structure.StructureState{},
		Session:   session.State{Levels: []session.Level{{Name: "ASIA_H", Price: 2020, Time: 500, Swept: false}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate without any Micro-layer swing to derive a price proxy from, got %d", len(got))
	}
}

func TestSessionLevel_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2019.5, 1000),
		Session:   session.State{Levels: []session.Level{{Name: "ASIA_H", Price: 2020, Time: 500, Swept: false}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate with zero ATR, got %d", len(got))
	}
}

func TestSessionLevel_SameSetupIsDeterministicAcrossEvaluations(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func() *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5,
			Structure: structStateWithPrice(2019.5, 1000),
			Session:   session.State{Levels: []session.Level{{Name: "ASIA_H", Price: 2020, Time: 500, Swept: false}}},
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

func TestSessionLevel_RejectsAMissingRequiredParameter(t *testing.T) {
	params := validParams()
	delete(params, "proximity_atr")
	if _, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Version: sessionlevel.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to fail closed on a missing required parameter")
	}
}

func TestSessionLevel_RequiredTimeframesIsM5(t *testing.T) {
	s := newStrategy(t, validParams())
	tfs := s.RequiredTimeframes()
	if len(tfs) != 1 || tfs[0] != market.M5 {
		t.Errorf("expected RequiredTimeframes()==[M5], got %v", tfs)
	}
}
