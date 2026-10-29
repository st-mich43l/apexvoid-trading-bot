package shockretest

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// The retracement band of the shock leg the first pullback is traded in, and the depth beyond which the pullback is a reversal. They are the
// definition of the thesis (the 50 to 61.8 percent retracement), not tunable coefficients.
const (
	RetracementNear = 0.5
	RetracementFar  = 0.618
	RetracementDead = 0.786
)

// Config is the Shock Retest v1 technical contract. Every coefficient is read from the strategy's parameters (config/analysis.yml); none has a code
// default. pip_size and the stop-budget keys are per-instrument facts the engine injects.
type Config struct {
	ATRLength, ATRWindowBars int

	ShockRangeATR, MinimumShockBodyRatio, ShockLegATR float64
	SettleBars, RetestMaxBars                         int
	MinimumWickRatio                                  float64
	DisplacementGradeATR                              float64
	ConfirmationMaxAgeBars                            int

	StopBufferPips, StopBufferATR float64
	MinimumTargetR                float64
	ExpiryHours                   float64
	SessionMomentumMinATR         float64

	PipSize float64
	Symbols []string
}

// ParseConfig reads the Shock Retest parameters; exported so replay and certification tooling configure exactly what production does.
func ParseConfig(params map[string]any) (Config, error) { return parseConfig(params) }

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	err := strategyutil.ParseNumbers("shock_retest", params, map[string]*float64{
		"shock_range_atr": &c.ShockRangeATR, "minimum_shock_body_ratio": &c.MinimumShockBodyRatio, "shock_leg_atr": &c.ShockLegATR,
		"minimum_wick_ratio": &c.MinimumWickRatio, "displacement_grade_atr": &c.DisplacementGradeATR,
		"stop_buffer_pips": &c.StopBufferPips, "stop_buffer_atr": &c.StopBufferATR, "minimum_target_r": &c.MinimumTargetR,
		"expiry_hours": &c.ExpiryHours, "session_momentum_minimum_atr": &c.SessionMomentumMinATR, "pip_size": &c.PipSize,
	}, map[string]*int{
		"atr_length": &c.ATRLength, "atr_window_bars": &c.ATRWindowBars, "settle_bars": &c.SettleBars, "retest_max_bars": &c.RetestMaxBars,
		"confirmation_max_age_bars": &c.ConfirmationMaxAgeBars,
	})
	if err != nil {
		return c, err
	}
	if c.Symbols, err = strategyutil.ParseSymbols(params); err != nil {
		return c, fmt.Errorf("shock_retest: %w", err)
	}
	return c, c.validate()
}

func (c Config) validate() error {
	switch {
	case c.ATRLength < 2 || c.ATRWindowBars < c.ATRLength+1:
		return fmt.Errorf("shock_retest: atr_length must be >= 2 and atr_window_bars must exceed it")
	case c.ShockRangeATR <= 0 || c.ShockLegATR <= 0 || c.MinimumShockBodyRatio < 0 || c.MinimumShockBodyRatio > 1:
		return fmt.Errorf("shock_retest: shock thresholds must be positive and the body ratio in [0, 1]")
	case c.SettleBars < 0 || c.SettleBars > 12 || c.RetestMaxBars < 1 || c.RetestMaxBars > 96:
		return fmt.Errorf("shock_retest: settle_bars must be in [0, 12] and retest_max_bars in [1, 96]")
	case c.MinimumWickRatio < 0 || c.MinimumWickRatio > 1 || c.DisplacementGradeATR < 0:
		return fmt.Errorf("shock_retest: minimum_wick_ratio must be in [0, 1]")
	case c.ConfirmationMaxAgeBars < 0 || c.ConfirmationMaxAgeBars > 12:
		return fmt.Errorf("shock_retest: confirmation_max_age_bars must be in [0, 12]")
	case c.StopBufferPips < 0 || c.StopBufferATR < 0 || c.StopBufferPips == 0 && c.StopBufferATR == 0:
		return fmt.Errorf("shock_retest: a stop needs a positive buffer (pips or ATR)")
	case c.MinimumTargetR <= 0 || c.ExpiryHours <= 0 || c.PipSize <= 0 || c.SessionMomentumMinATR < 0:
		return fmt.Errorf("shock_retest: minimum_target_r, expiry_hours and pip_size must be > 0")
	}
	return nil
}
