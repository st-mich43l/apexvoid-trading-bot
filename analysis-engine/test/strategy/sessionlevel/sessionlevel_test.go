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
		"proximal_band_atr":           0.5,
		"reaction_lookback_bars":      3.0,
		"engulfing_minimum_range_atr": 0.5,
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

// priceBar is one closed bar at price: the real latest close the strategy
// now judges proximity against (it used to read the last micro swing).
func priceBar(price float64, t int64) []market.Candle {
	return []market.Candle{{Time: t, Open: price, High: price, Low: price, Close: price}}
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
		Structure: structStateWithPrice(2019.5, 1000), Candles: priceBar(2019.5, 1000),
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
		Structure: structStateWithPrice(2020.5, 1000), Candles: priceBar(2020.5, 1000),
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
		Structure: structStateWithPrice(2019.5, 1000), Candles: priceBar(2019.5, 1000),
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
		Structure: structStateWithPrice(2019.5, 1000), Candles: priceBar(2019.5, 1000),
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
		Structure: structStateWithPrice(1990.0, 1000), Candles: priceBar(1990.0, 1000), // 30 ATR away, proximity_atr=1.0
		Session:   session.State{Levels: []session.Level{{Name: "ASIA_H", Price: 2020, Time: 500, Swept: false}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a level too far from current price, got %d", len(got))
	}
}

func TestSessionLevel_NoCandlesYetProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structure.StructureState{},
		Session:   session.State{Levels: []session.Level{{Name: "ASIA_H", Price: 2020, Time: 500, Swept: false}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate without a closed bar to judge proximity against, got %d", len(got))
	}
}

func TestSessionLevel_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Structure: structStateWithPrice(2019.5, 1000), Candles: priceBar(2019.5, 1000),
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
			Structure: structStateWithPrice(2019.5, 1000), Candles: priceBar(2019.5, 1000),
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

func sweptLowContext(bars []market.Candle) *context.MarketContext {
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles:   bars,
		Session:   session.State{Levels: []session.Level{{Name: "PDL", Price: 2020, Time: 500, Swept: true}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquiditySellSide, Low: 2010, High: 2011}, {Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	return ctx
}

// A swept level is valid only through a reclaim-type confirmation: this bar
// takes the level out (low 2019.0 < band low 2019.5) and closes back inside,
// bullish - a strong reclaim.
func TestSessionLevel_SweptLevelWithAStrongReclaimProducesAConfirmedCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	got := s.Evaluate(sweptLowContext([]market.Candle{{Time: 1000, Open: 2019.6, High: 2020.4, Low: 2019.0, Close: 2020.2}}))
	if len(got) != 1 {
		t.Fatalf("expected the confirmed reclaim candidate only (a swept level has no resting candidate), got %d", len(got))
	}
	c := got[0]
	if c.Direction != market.Buy || c.Reaction == nil || c.Reaction.Pattern != "strong_reclaim" || c.Reaction.ReactionType != "rejection" {
		t.Fatalf("expected a BUY strong_reclaim confirmation, got %+v / %+v", c.Direction, c.Reaction)
	}
	if err := c.Validate(); err != nil {
		t.Fatalf("confirmed candidate must validate: %v", err)
	}
}

// ...but a plain wick rejection after the level was already taken out is not
// a reaction off that level (legacy rule).
func TestSessionLevel_SweptLevelWithOnlyAWickRejectionProducesNothing(t *testing.T) {
	s := newStrategy(t, validParams())
	got := s.Evaluate(sweptLowContext([]market.Candle{{Time: 1000, Open: 2020.3, High: 2020.55, Low: 2019.6, Close: 2020.5}}))
	if len(got) != 0 {
		t.Fatalf("a non-reclaim confirmation on a swept level must not produce a candidate, got %d", len(got))
	}
}
