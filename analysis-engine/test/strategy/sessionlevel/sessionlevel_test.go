package sessionlevel_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// The detector's behaviour is pinned in internal/strategy/sessionlevel (fixtures,
// negatives, invariance) and certified on real captures in test/slreplay. These
// tests pin the contract's edges as the catalog sees it.

func validParams() map[string]any {
	return legacyfixture.Params(map[string]any{
		"atr_length": 14.0, "atr_window_bars": 140.0, "pivot_bars": 2.0, "episode_lookback_bars": 48.0,
		"asia_start_hour": 22.0, "london_start_hour": 7.0, "ny_start_hour": 13.0, "daily_rollover_utc_hour": 21.0,
		"session_start_tolerance_minutes": 90.0, "session_end_tolerance_minutes": 90.0, "session_max_gap_minutes": 60.0, "session_minimum_coverage": 0.8,
		"period_start_tolerance_minutes": 120.0, "period_end_tolerance_minutes": 120.0, "period_max_gap_minutes": 150.0, "period_minimum_coverage": 0.85,
		"session_max_age_hours": 30.0, "day_max_age_hours": 72.0, "week_max_age_hours": 168.0,
		"touch_tolerance_pips": 2.0, "touch_tolerance_atr": 0.10, "approach_lookback_bars": 24.0, "approach_min_distance_atr": 0.5,
		"touch_separation_bars": 3.0, "maximum_prior_touches": 1.0, "hold_buffer_pips": 1.0, "hold_buffer_atr": 0.05,
		"fail_buffer_pips": 3.0, "fail_buffer_atr": 0.25, "sweep_threshold_pips": 3.0, "sweep_threshold_atr": 0.10,
		"reclaim_buffer_pips": 1.0, "reclaim_buffer_atr": 0.05, "reclaim_max_bars": 6.0, "breakout_accept_closes": 2.0,
		"breakout_buffer_pips": 2.0, "breakout_buffer_atr": 0.15, "rejection_close_strength": 0.6, "rejection_wick_ratio": 0.25,
		"rejection_body_ratio": 0.6, "displacement_grade_atr": 1.0, "confirmation_window_bars": 3.0, "confirmation_max_age_bars": 2.0,
		"max_episodes_per_level": 3.0, "entry_band_atr": 0.5, "entry_tolerance_pips": 1.0, "entry_tolerance_atr": 0.15,
		"maximum_entry_distance_atr": 1.5, "invalidation_buffer_atr": 0.25, "target_lookback_bars": 96.0, "target_swing_bars": 12.0,
		"target_swing_atr": 1.0, "minimum_target_room_atr": 0.55, "minimum_reward_risk": 1.15, "expiry_hours": 4.0, "pip_size": 0.1,
	})
}

func newStrategy(t *testing.T, params map[string]any) strategy.Strategy {
	t.Helper()
	s, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Version: sessionlevel.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatalf("sessionlevel.New: %v", err)
	}
	return s
}

func TestSessionLevel_IsVersionFour(t *testing.T) {
	if sessionlevel.Version != "v4" {
		t.Fatalf("version = %q", sessionlevel.Version)
	}
}

func TestSessionLevel_RejectsMissingOrInvalidConfiguration(t *testing.T) {
	if _, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Parameters: map[string]any{}}); err == nil {
		t.Fatal("missing configuration must fail closed")
	}
	bad := validParams()
	bad["entry_band_atr"] = 0.0
	if _, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Parameters: bad}); err == nil {
		t.Fatal("a zero entry band must be rejected")
	}
	// The v3 parameter set (a frozen detector with only three own coefficients) is not
	// a v4 configuration: nothing silently falls back to a default.
	v3 := legacyfixture.Params(map[string]any{"invalidation_buffer_atr": 0.5, "minimum_target_distance_atr": 1.0, "expiry_hours": 24.0, "proximal_band_atr": 0.5})
	if _, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Parameters: v3}); err == nil {
		t.Fatal("the v3 parameter set must not construct a v4 strategy")
	}
}

func TestSessionLevel_WithoutCandlesPublishesNothing(t *testing.T) {
	s := newStrategy(t, validParams())
	ctx := &context.MarketContext{Symbol: "XAU", Timeframes: map[market.Timeframe]*context.TimeframeContext{}, Volatility: context.VolatilityContext{ATR: 1}}
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatalf("published without candles: %+v", got)
	}
	if got := s.Evaluate(nil); len(got) != 0 {
		t.Fatalf("published without a context: %+v", got)
	}
}

// A quiet market: candles nobody reacted to, however many levels exist, publish
// nothing (a resting level is never an opportunity).
func TestSessionLevel_ARestingLevelWithoutAReactionIsNeverPublished(t *testing.T) {
	s := newStrategy(t, validParams())
	const start = int64(1_760_400_000) - 1_760_400_000%300 - 3*24*3600
	var bars []market.Candle
	for i := 0; i < 3*288; i++ {
		bars = append(bars, market.Candle{Time: start + int64(300*i), Open: 2010, High: 2011, Low: 2009, Close: 2010.4})
	}
	ctx := legacyfixture.Context(bars, 1, nil)
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatalf("a level nobody reacted to was published: %+v", got)
	}
}
