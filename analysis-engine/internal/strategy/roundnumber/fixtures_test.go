package roundnumber

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

// fixtureHour is an hour-aligned base time inside one trading day (the calendar rolls over at 21:00 UTC; this is 10:00 UTC on some day).
const fixtureHour = int64(1_760_000_400 - 1_760_000_400%86400 + 10*3600)

// roundParams is the strategy's own parameter set as config/analysis.yml declares it (XAU scale), merged with the shared detector contract and the
// per-instrument facts the engine injects.
func roundParams(override map[string]any) map[string]any {
	own := map[string]any{
		"handle_step": 10.0, "major_step": 50.0, "max_handle_distance_atr": 3.0, "atr_length": 14.0, "atr_window_bars": 140.0,
		"minimum_sweep_pips": 5.0, "maximum_sweep_pips": 40.0, "minimum_reclaim_pips": 2.0, "reclaim_max_bars": 3.0, "acceptance_closes": 2.0,
		"confirmation_mode": "mss", "structure_pivot_bars": 2.0, "structure_lookback_bars": 24.0, "mss_max_bars": 6.0,
		"minimum_mss_body_ratio": 0.5, "minimum_mss_displacement_atr": 0.5, "minimum_mss_close_strength": 0.6, "confirmation_max_age_bars": 2.0,
		"entry_band_pips": 15.0, "break_buffer_pips": 10.0, "minimum_break_body_ratio": 0.6, "minimum_break_range_atr": 1.2,
		"retest_max_bars": 12.0, "retest_tolerance_pips": 5.0, "displacement_grade_atr": 1.0, "minimum_wick_ratio": 0.4,
		"stop_buffer_pips": 5.0, "stop_buffer_atr": 0.10, "minimum_target_r": 1.0, "expiry_hours": 2.0,
		"session_momentum_minimum_atr": 1.0, "trading_day_rollover_utc_hour": 21.0, "symbols": []any{"XAU"}, "execution_stop_max_pips": 70.0, "execution_stop_min_pips": 50.0,
		"execution_pip_size": 0.1,
	}
	for k, v := range override {
		own[k] = v
	}
	return legacyfixture.Params(own)
}

func testConfig(t *testing.T, override map[string]any) Config {
	t.Helper()
	cfg, err := ParseConfig(roundParams(override))
	if err != nil {
		t.Fatal(err)
	}
	return cfg
}

// bullishStopRun is the canonical accepted XAU BUY at the 4110 handle: price above the handle makes a swing high (4115.0), falls to the handle, a
// candle sweeps 10 pips below it and closes back above, and a displacement candle closes above the swing high. It returns the series through the
// structure-shift candle (the last candle) and the named candle indices.
func bullishStopRun() (bars []market.Candle, idx runIndex) {
	s := scribe.New(fixtureHour-60*300, 4113)
	s.Filler(60, 4113)
	s.Bar(4114.0, 0.6, 0.3) // climbs
	s.Bar(4114.6, 0.4, 0.2) // swing high 4115.0 (pivot, n=2)
	s.Bar(4113.4, 0.3, 0.5)
	s.Bar(4112.2, 0.3, 0.5)
	s.Bar(4110.6, 0.3, 0.3) // down to the handle, no sweep yet (low 4110.3)
	idx.sweep = s.Index()
	s.Bar(4110.9, 0.2, 1.6) // sweep: low 4109.0 (10 pips under 4110), closes back above on the same candle
	s.Bar(4111.5, 0.3, 0.2)
	idx.shift = s.Index()
	s.Bar(4115.6, 0.2, 0.1) // closes above 4115.0: structure shift
	idx.reclaim = idx.sweep
	return s.Bars, idx
}

type runIndex struct{ sweep, reclaim, shift int }
