package impulseretrace

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// parseConfig reads every parameter and fails closed: a missing or invalid value stops the engine at startup, a strategy never trades a guessed value.
func parseConfig(params map[string]any) (Config, error) {
	var cfg Config
	err := strategyutil.ParseNumbers("impulse_retrace", params, map[string]*float64{
		"pip_size": &cfg.PipSize, "stop_minimum_pips": &cfg.StopMinimumPips, "stop_maximum_pips": &cfg.StopMaximumPips,
		"minimum_net_target_pips": &cfg.MinimumNetTargetPips, "impulse_body_atr": &cfg.ImpulseBodyATR, "retrace_fraction": &cfg.RetraceFraction,
		"stop_buffer_atr": &cfg.StopBufferATR, "entry_width_pips": &cfg.EntryWidthPips, "entry_window_minutes": &cfg.EntryWindowMinutes,
	}, map[string]*int{
		"cooldown_bars": &cfg.CooldownBars, "atr_length": &cfg.ATRLength, "atr_window_bars": &cfg.ATRWindowBars,
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
	case cfg.ImpulseBodyATR <= 0:
		return Config{}, fmt.Errorf("impulse_body_atr must be positive")
	case cfg.RetraceFraction <= 0 || cfg.RetraceFraction >= 1:
		return Config{}, fmt.Errorf("retrace_fraction must be in (0, 1)")
	case cfg.StopBufferATR < 0:
		return Config{}, fmt.Errorf("stop_buffer_atr must not be negative")
	case cfg.EntryWidthPips <= 0:
		return Config{}, fmt.Errorf("entry_width_pips must be positive")
	case cfg.EntryWindowMinutes <= 0:
		return Config{}, fmt.Errorf("entry_window_minutes must be positive")
	case cfg.CooldownBars < 1:
		return Config{}, fmt.Errorf("cooldown_bars must be at least 1")
	case cfg.ATRLength < 2 || cfg.ATRWindowBars <= cfg.ATRLength:
		return Config{}, fmt.Errorf("atr_length must be at least 2 and atr_window_bars must exceed it")
	}
	return cfg, nil
}
