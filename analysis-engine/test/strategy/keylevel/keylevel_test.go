package keylevel_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	strategykeylevel "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func validParams() map[string]any {
	return map[string]any{
		"minimum_touches":             2.0,
		"minimum_strength":            0.3,
		"proximity_atr":               1.0,
		"invalidation_buffer_atr":     0.5,
		"minimum_target_distance_atr": 1.0,
		"expiry_hours":                24.0,
		"breakout_accept_bars":        2.0,
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

// buyConfirmationCandles is a two-bar closed window whose last bar touches
// [low, high] and closes bullish above it in the same bar — a same-bar
// touch-and-confirm rejection. c0 keeps the level's own role Ambiguous
// (only one close beyond the band, short of breakout_accept_bars=2).
func buyConfirmationCandles(precedingClose float64) []market.Candle {
	return []market.Candle{
		{Time: 1000, Open: precedingClose - 0.1, High: precedingClose + 0.2, Low: precedingClose - 0.2, Close: precedingClose},
		{Time: 1060, Open: 2019.7, High: 2021.0, Low: 2019.6, Close: 2020.8},
	}
}

func sellConfirmationCandles(precedingClose float64) []market.Candle {
	return []market.Candle{
		{Time: 1000, Open: precedingClose - 0.1, High: precedingClose + 0.2, Low: precedingClose - 0.2, Close: precedingClose},
		{Time: 1060, Open: 2020.3, High: 2020.4, Low: 2019.2, Close: 2019.2},
	}
}

func baseLevel() keylevel.Level {
	return keylevel.Level{ID: "level-2020", Price: 2020, Kind: keylevel.KindReaction, Touches: 3, Band: 0.5, Strength: 0.8}
}

func TestKeyLevel_PriceAboveLevelProducesABuyCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles:   buyConfirmationCandles(2020.0), // last close 2020.8 > band high 2020.5 -> naive support -> BUY
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Direction != market.Buy {
		t.Errorf("expected BUY direction when price sits above the level (support), got %v", candidates[0].Direction)
	}
	if candidates[0].Reaction == nil || candidates[0].Reaction.ReactionType != "rejection" {
		t.Errorf("expected a confirmed rejection reaction, got %+v", candidates[0].Reaction)
	}
	// The level's own stable ID, never the full local setup key (which also
	// embeds this reaction's own touch/confirmation bar times) - two
	// confirmations of the SAME level must correlate as one thesis.
	if candidates[0].StructuralID != "level-2020" {
		t.Errorf("expected StructuralID to be the level's own ID, got %q", candidates[0].StructuralID)
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
		Candles:   sellConfirmationCandles(2020.0), // last close 2019.2 < band low 2019.5 -> naive resistance -> SELL
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
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
	level := baseLevel()
	level.Touches = 1 // below configured minimum_touches=2
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles:   buyConfirmationCandles(2020.0),
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{level}},
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
		// last close 2050, 30 ATR from the level (proximity_atr=1.0)
		Candles:   []market.Candle{{Time: 1000, Open: 2050, High: 2050.2, Low: 2049.8, Close: 2050}},
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a level too far from current price, got %d", len(got))
	}
}

func TestKeyLevel_WeakLevelProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	level := baseLevel()
	level.Strength = 0.1 // below minimum_strength=0.3
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles:   buyConfirmationCandles(2020.0),
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{level}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a below-threshold-strength level, got %d", len(got))
	}
}

// TestKeyLevel_NoClosedCandlesYetProducesNoCandidate is the "insufficient
// history" case: a fresh symbol has no closed M5 bar yet, so there is no
// current price and no possible confirmation.
func TestKeyLevel_NoClosedCandlesYetProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles:   nil,
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate without any closed candle to derive a current price from, got %d", len(got))
	}
}

func TestKeyLevel_ZeroATRProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles:   buyConfirmationCandles(2020.0),
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate with zero ATR, got %d", len(got))
	}
}

func TestKeyLevel_NoConfirmedReactionProducesNoCandidate(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		// Price sits above the level (proximity-eligible) but never closes
		// back above the band after touching it: a resting observation, not
		// a confirmed trade.
		Candles:   []market.Candle{{Time: 1000, Open: 2020.9, High: 2021.0, Low: 2020.7, Close: 2020.9}},
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a level near price with no closed-bar rejection yet, got %d", len(got))
	}
}

// TestKeyLevel_BrokenRoleIsSkipped proves a level two consecutive closes
// have already accepted beyond is reported BROKEN by keylevel.Role (ported
// 1:1 from key_level_role.py) and never re-traded here, even though its
// last bar's shape would otherwise look like a valid confirmed rejection.
// Break & Retest/Trendline own that reinterpretation, not Key Level.
func TestKeyLevel_BrokenRoleIsSkipped(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		// Two consecutive closes above band high (2020.5): 2020.9 then
		// 2020.8, both > 2020.5 -> above=2 >= breakout_accept_bars(2).
		Candles: []market.Candle{
			{Time: 1000, Open: 2020.6, High: 2021.0, Low: 2020.4, Close: 2020.9},
			{Time: 1060, Open: 2019.7, High: 2021.0, Low: 2019.6, Close: 2020.8},
		},
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Errorf("expected no candidate for a level already accepted through (BROKEN role), got %d", len(got))
	}
}

// TestKeyLevel_OpposingZoneWidensTheReactionBand proves a real, unmitigated
// opposing-side zone overlapping the level's own band (opposing.go, ported
// from detectors.py::_opposing_zone_contradicts) is not ignored: the
// reaction window widens to cover it, and the resulting candidate's own
// entry band reflects that widened window, not just the level's narrow
// band. (Proving the widening also *changes which direction ends up
// confirmed*, away from the naive price-position guess, needs a
// multi-bar reaction lookback this port does not implement — see
// confirmation.go's doc comment; this test proves the mechanism the user
// asked about actually fires and affects the published contract.)
func TestKeyLevel_OpposingZoneWidensTheReactionBand(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles: []market.Candle{
			{Time: 1000, Open: 2019.9, High: 2020.1, Low: 2019.8, Close: 2020.0},
			// Closes at 2020.9: above the widened band high (2020.8), the
			// level's own narrow band high (2020.5), and still within
			// proximity_atr(1.0) of the level (2020).
			{Time: 1060, Open: 2020.6, High: 2021.0, Low: 2020.5, Close: 2020.9},
		},
		KeyLevel: keylevel.State{Levels: []keylevel.Level{baseLevel()}},
		Zones: zone.ZoneState{Zones: []zone.Zone{{
			ID: "supply-1", Kind: zone.KindSupply, State: zone.StateFresh, Low: 2020.3, High: 2020.8,
		}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	c := candidates[0]
	if c.Direction != market.Buy {
		t.Fatalf("expected BUY, got %v", c.Direction)
	}
	if c.Entry.Low != 2019.5 || c.Entry.High != 2020.8 {
		t.Errorf("expected the entry band widened to the opposing zone's own edge (2019.5, 2020.8), got (%v, %v)", c.Entry.Low, c.Entry.High)
	}
	found := false
	for _, e := range c.Evidence {
		if e.Code == "m5_key_level_opposing_zone_widened" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected the m5_key_level_opposing_zone_widened evidence code, got %+v", c.Evidence)
	}
}

// TestKeyLevel_MitigatedOpposingZoneIsIgnored proves an already-mitigated
// opposing zone is not a live barrier (Python's "not zone.mitigated"):
// the naive, unwidened guess is used and the candidate looks exactly like
// TestKeyLevel_PriceAboveLevelProducesABuyCandidate.
func TestKeyLevel_MitigatedOpposingZoneIsIgnored(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := baseContext(1.0)
	ctx.Timeframes[market.M5] = &context.TimeframeContext{
		Timeframe: market.M5,
		Candles:   buyConfirmationCandles(2020.0),
		KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
		Zones: zone.ZoneState{Zones: []zone.Zone{{
			ID: "supply-1", Kind: zone.KindSupply, State: zone.StateMitigated, Low: 2020.3, High: 2022.0,
		}}},
		Liquidity: liquidity.LiquidityState{Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 2031, High: 2032}}},
	}
	candidates := s.Evaluate(ctx)
	if len(candidates) != 1 {
		t.Fatalf("expected exactly 1 candidate, got %d", len(candidates))
	}
	if candidates[0].Entry.High != 2020.5 {
		t.Errorf("expected the unwidened level band (high=2020.5); a mitigated zone must not widen it, got high=%v", candidates[0].Entry.High)
	}
}

func TestKeyLevel_SameSetupIsDeterministicAcrossEvaluations(t *testing.T) {
	s := newStrategy(t, validParams())
	build := func() *context.MarketContext {
		ctx := baseContext(1.0)
		ctx.Timeframes[market.M5] = &context.TimeframeContext{
			Timeframe: market.M5,
			Candles:   buyConfirmationCandles(2020.0),
			KeyLevel:  keylevel.State{Levels: []keylevel.Level{baseLevel()}},
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

func TestKeyLevel_RejectsZeroBreakoutAcceptBars(t *testing.T) {
	params := validParams()
	params["breakout_accept_bars"] = 0.0
	if _, err := strategykeylevel.New(strategy.Config{ID: strategykeylevel.ID, Version: strategykeylevel.Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("expected New to reject breakout_accept_bars < 1")
	}
}

func TestKeyLevel_RequiredTimeframesIsM5(t *testing.T) {
	s := newStrategy(t, validParams())
	tfs := s.RequiredTimeframes()
	if len(tfs) != 1 || tfs[0] != market.M5 {
		t.Errorf("expected RequiredTimeframes()==[M5], got %v", tfs)
	}
}
