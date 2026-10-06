package scalpbreakoutretest

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// parseConfig reads the strategy's parameters. Every tunable is required: a
// missing key is a configuration error, never a hidden default.
func parseConfig(params map[string]any) (Config, error) {
	var cfg Config
	var err error
	num := func(key string) float64 {
		if err != nil {
			return 0
		}
		var v float64
		v, err = strategyutil.Float(params, key)
		return v
	}
	count := func(key string) int {
		if err != nil {
			return 0
		}
		var v int
		v, err = strategyutil.Int(params, key)
		return v
	}
	flag := func(key string) bool {
		if err != nil {
			return false
		}
		v, ok := params[key].(bool)
		if !ok {
			err = fmt.Errorf("parameter %q must be a boolean", key)
		}
		return v
	}

	cfg.PipSize = num("pip_size")
	cfg.PriceDigits = count("price_digits")
	cfg.SetupWindowBars = count("setup_window_bars")
	cfg.ConfirmationWindowBars = count("confirmation_window_bars")
	cfg.ConfirmationLookback = count("confirmation_lookback_bars")
	cfg.ContextMaxAgeSeconds = int64(count("context_max_age_seconds"))
	cfg.ContextATRBars = count("context_atr_bars")
	cfg.ActiveRangeBars = count("active_range_bars")
	cfg.ExpiryMinutes = num("expiry_minutes")

	cfg.BoxMaxATR = num("box_max_atr")
	cfg.MinBreakATR = num("min_break_atr")
	cfg.TouchTolATR = num("touch_tol_atr")
	cfg.MinBoxBars = count("min_box_bars")
	cfg.MaxBoxBars = count("max_box_bars")
	cfg.MinTouchesPerSide = count("min_touches_per_side")
	cfg.RetestLookbackBars = count("retest_lookback_bars")
	cfg.RequireRetestRejection = flag("require_retest_rejection")

	cfg.V2Enabled = flag("v2_enabled")
	cfg.CrossToleranceATR = num("breakout_cross_tolerance_atr")
	cfg.BreakoutMarginATR = num("breakout_margin_atr")
	cfg.AcceptanceBars = count("acceptance_bars")
	cfg.AcceptanceRequired = count("acceptance_required_closes")
	cfg.MinRetestDelayBars = count("min_retest_delay_bars")
	cfg.MaxRetestDelayBars = count("max_retest_delay_bars")
	cfg.RetestFrontRunATR = num("retest_front_run_atr")
	cfg.MaxRetestPenetrationATR = num("max_retest_penetration_atr")
	cfg.MinQualityScore = num("min_quality_score")
	cfg.EnableStructureFlip = flag("enable_structure_flip")
	cfg.EnableM5KeyLevels = flag("enable_m5_key_levels")
	cfg.EnableLiquidityLevel = flag("enable_liquidity_level")
	cfg.SwingMinAgeBars = count("swing_min_age_bars")
	cfg.SwingMaxAgeBars = count("swing_max_age_bars")
	cfg.SwingMinSpacingATR = num("swing_min_spacing_atr")
	cfg.M5StructureMinTouches = count("m5_structure_min_touches")

	cfg.MicroSwingLookback = count("micro_swing_lookback")
	cfg.EqualToleranceFracPip = num("equal_tolerance_pip_fraction")
	cfg.SwingFractalN = count("swing_fractal_n")
	cfg.LevelClusterATR = num("level_cluster_atr")
	cfg.LevelRoundStep = num("level_round_step")
	cfg.LevelMinimumTouches = count("level_minimum_touches")
	cfg.LevelMaxClusterSpan = num("level_max_cluster_span_multiple")

	cfg.BufferM1ATRMultiple = num("buffer_m1_atr_multiple")
	cfg.BufferMinSpreadMultiple = num("buffer_minimum_spread_multiple")
	cfg.MaximumSpreadPips = num("maximum_spread_pips")
	cfg.StopMinimumPips = num("stop_minimum_pips")
	cfg.StopMaximumPips = num("stop_maximum_pips")
	cfg.MinimumNetTargetPips = num("minimum_net_target_pips")
	if err != nil {
		return Config{}, err
	}

	mode, ok := params["confirmation_mode"].(string)
	if !ok || (mode != "reclaim_close" && mode != confirmationModeHighBreak) {
		return Config{}, fmt.Errorf("confirmation_mode must be reclaim_close or retest_high_break")
	}
	cfg.ConfirmationMode = mode
	if raw, present := params["max_break_delay_bars"]; present && raw != nil {
		n, e := strategyutil.Int(params, "max_break_delay_bars")
		if e != nil {
			return Config{}, e
		}
		cfg.MaxBreakDelayBars = &n
	}
	ladder, ok := params["reward_risk_ladder"].([]any)
	if !ok || len(ladder) == 0 {
		return Config{}, fmt.Errorf("reward_risk_ladder must be a non-empty list")
	}
	for _, item := range ladder {
		switch v := item.(type) {
		case float64:
			cfg.RewardRiskLadder = append(cfg.RewardRiskLadder, v)
		case int:
			cfg.RewardRiskLadder = append(cfg.RewardRiskLadder, float64(v))
		default:
			return Config{}, fmt.Errorf("reward_risk_ladder entries must be numbers")
		}
	}
	if raw, present := params["symbols"]; present {
		list, ok := raw.([]any)
		if !ok {
			return Config{}, fmt.Errorf("symbols must be a list")
		}
		for _, item := range list {
			s, ok := item.(string)
			if !ok {
				return Config{}, fmt.Errorf("symbols entries must be strings")
			}
			cfg.Symbols = append(cfg.Symbols, s)
		}
	}
	switch {
	case cfg.PipSize <= 0, cfg.SetupWindowBars < 10, cfg.ConfirmationWindowBars < 15, cfg.ConfirmationLookback < 1,
		cfg.ContextATRBars < 1, cfg.ActiveRangeBars < 2, cfg.ExpiryMinutes <= 0, cfg.AcceptanceBars < 1, cfg.AcceptanceRequired < 1,
		cfg.StopMinimumPips <= 0, cfg.StopMaximumPips < cfg.StopMinimumPips, cfg.MicroSwingLookback < 1:
		return Config{}, fmt.Errorf("invalid parameters")
	}
	return cfg, nil
}
