package rangescalp

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// parseConfig reads every parameter and fails closed: a missing or invalid value stops the engine at startup, a strategy never trades a guessed value.
func parseConfig(params map[string]any) (Config, error) {
	var cfg Config
	err := strategyutil.ParseNumbers("range_scalp", params, map[string]*float64{
		"pip_size": &cfg.PipSize, "stop_minimum_pips": &cfg.StopMinimumPips, "stop_maximum_pips": &cfg.StopMaximumPips,
		"minimum_net_target_pips": &cfg.MinimumNetTargetPips, "maximum_efficiency_ratio": &cfg.MaximumEfficiencyRatio,
		"minimum_range_atr": &cfg.MinimumRangeATR, "maximum_range_atr": &cfg.MaximumRangeATR, "edge_fraction": &cfg.EdgeFraction,
		"stop_buffer_atr": &cfg.StopBufferATR, "entry_width_pips": &cfg.EntryWidthPips, "expiry_minutes": &cfg.ExpiryMinutes,
		"barrier_buffer_atr": &cfg.BarrierBufferATR, "barrier_max_width_atr": &cfg.BarrierMaxWidthATR, "barrier_max_width_pips": &cfg.BarrierMaxWidthPips,
	}, map[string]*int{
		"range_lookback_bars": &cfg.RangeLookbackBars, "cooldown_bars": &cfg.CooldownBars, "maximum_signals_per_day": &cfg.MaximumSignalsPerDay,
		"atr_length": &cfg.ATRLength, "atr_window_bars": &cfg.ATRWindowBars, "trading_day_rollover_utc_hour": &cfg.TradingDayRolloverHour,
	})
	if err != nil {
		return Config{}, err
	}
	if cfg.Symbols, err = strategyutil.ParseSymbols(params); err != nil {
		return Config{}, err
	}
	ladder, ok := params["reward_risk_ladder"].([]any)
	if !ok || len(ladder) == 0 {
		return Config{}, fmt.Errorf("reward_risk_ladder must be a non-empty list")
	}
	for _, item := range ladder {
		var v float64
		switch n := item.(type) {
		case float64:
			v = n
		case int:
			v = float64(n)
		default:
			return Config{}, fmt.Errorf("reward_risk_ladder entries must be numbers")
		}
		if v <= 0 {
			return Config{}, fmt.Errorf("reward_risk_ladder entries must be positive")
		}
		cfg.RewardRiskLadder = append(cfg.RewardRiskLadder, v)
	}
	switch {
	case cfg.PipSize <= 0:
		return Config{}, fmt.Errorf("pip_size must be positive")
	case cfg.StopMinimumPips <= 0 || cfg.StopMaximumPips < cfg.StopMinimumPips:
		return Config{}, fmt.Errorf("stop_minimum_pips must be positive and at most stop_maximum_pips")
	case cfg.MinimumNetTargetPips < 0:
		return Config{}, fmt.Errorf("minimum_net_target_pips must not be negative")
	case cfg.RangeLookbackBars < 5:
		return Config{}, fmt.Errorf("range_lookback_bars must be at least 5")
	case cfg.MaximumEfficiencyRatio <= 0 || cfg.MaximumEfficiencyRatio > 1:
		return Config{}, fmt.Errorf("maximum_efficiency_ratio must be in (0, 1]")
	case cfg.MinimumRangeATR <= 0 || cfg.MaximumRangeATR <= cfg.MinimumRangeATR:
		return Config{}, fmt.Errorf("range ATR bounds need 0 < minimum_range_atr < maximum_range_atr")
	case cfg.EdgeFraction <= 0 || cfg.EdgeFraction >= 0.5:
		return Config{}, fmt.Errorf("edge_fraction must be in (0, 0.5)")
	case cfg.StopBufferATR < 0:
		return Config{}, fmt.Errorf("stop_buffer_atr must not be negative")
	case cfg.EntryWidthPips <= 0:
		return Config{}, fmt.Errorf("entry_width_pips must be positive")
	case cfg.CooldownBars < 1 || cfg.MaximumSignalsPerDay < 1:
		return Config{}, fmt.Errorf("cooldown_bars and maximum_signals_per_day must be at least 1")
	case cfg.BarrierBufferATR < 0 || cfg.BarrierMaxWidthATR <= 0 || cfg.BarrierMaxWidthPips <= 0:
		return Config{}, fmt.Errorf("barrier_buffer_atr must not be negative and barrier_max_width_atr and barrier_max_width_pips must be positive")
	case cfg.ExpiryMinutes <= 0:
		return Config{}, fmt.Errorf("expiry_minutes must be positive")
	case cfg.ATRLength < 2 || cfg.ATRWindowBars <= cfg.ATRLength:
		return Config{}, fmt.Errorf("atr_length must be at least 2 and atr_window_bars must exceed it")
	case cfg.TradingDayRolloverHour < 0 || cfg.TradingDayRolloverHour > 23:
		return Config{}, fmt.Errorf("trading_day_rollover_utc_hour must be in [0, 23]")
	}
	return cfg, nil
}
