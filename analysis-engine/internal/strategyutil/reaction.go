package strategyutil

import (
	"fmt"
)

// ReactionConfig is the legacy Python reaction-confirmation tuning
// (execution.policy.structural_reaction_lookback_bars and
// engulfing_minimum_range_atr), read from a strategy's own parameters.
type ReactionConfig struct {
	LookbackBars             int
	EngulfingMinimumRangeATR float64
}

// ParseReactionConfig reads reaction_lookback_bars and
// engulfing_minimum_range_atr; both are required (no hidden defaults).
func ParseReactionConfig(params map[string]any) (ReactionConfig, error) {
	lookback, err := Int(params, "reaction_lookback_bars")
	if err != nil {
		return ReactionConfig{}, err
	}
	if lookback < 1 {
		return ReactionConfig{}, fmt.Errorf("reaction_lookback_bars must be >= 1")
	}
	engulfing, err := Float(params, "engulfing_minimum_range_atr")
	if err != nil {
		return ReactionConfig{}, err
	}
	if engulfing < 0 {
		return ReactionConfig{}, fmt.Errorf("engulfing_minimum_range_atr must be >= 0")
	}
	return ReactionConfig{LookbackBars: lookback, EngulfingMinimumRangeATR: engulfing}, nil
}
