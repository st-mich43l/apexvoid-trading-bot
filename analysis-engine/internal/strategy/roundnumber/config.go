package roundnumber

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Config is the Round Number v1 technical contract. Every coefficient is read from the strategy's parameters (config/analysis.yml); none has a code
// default, so a missing key fails construction instead of silently trading a guessed value. pip_size, execution_stop_max_pips (the budget keys) and
// trading_day_rollover_utc_hour are per-instrument facts the engine injects.
type Config struct {
	HandleStep, MajorStep, MaxHandleDistanceATR float64
	ATRLength, ATRWindowBars                    int

	// Setup A: the stop run.
	MinimumSweepPips, MaximumSweepPips float64
	MinimumReclaimPips                 float64
	ReclaimMaxBars, AcceptanceCloses   int
	ConfirmationMode                   string
	StructurePivotBars                 int
	StructureLookbackBars              int
	MSSMaxBars                         int
	MinimumMSSBodyRatio                float64
	MinimumMSSDisplacementATR          float64
	MinimumMSSCloseStrength            float64
	ConfirmationMaxAgeBars             int

	// Setup B: the accepted break.
	EntryBandPips, BreakBufferPips float64
	MinimumBreakBodyRatio          float64
	MinimumBreakRangeATR           float64
	RetestMaxBars                  int
	RetestTolerancePips            float64
	DisplacementGradeATR           float64

	MinimumWickRatio float64
	StopBufferPips   float64
	StopBufferATR    float64
	MinimumTargetR   float64
	ExpiryHours      float64

	SessionMomentumMinimumATR float64
	PipSize                   float64
	TradingDayRolloverHour    int
	Symbols                   []string
}

// ParseConfig reads the Round Number parameters; exported so replay and certification tooling configure exactly what production does.
func ParseConfig(params map[string]any) (Config, error) { return parseConfig(params) }

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	err := strategyutil.ParseNumbers("round_number", params, map[string]*float64{
		"handle_step": &c.HandleStep, "major_step": &c.MajorStep, "max_handle_distance_atr": &c.MaxHandleDistanceATR,
		"minimum_sweep_pips": &c.MinimumSweepPips, "maximum_sweep_pips": &c.MaximumSweepPips, "minimum_reclaim_pips": &c.MinimumReclaimPips,
		"minimum_mss_body_ratio": &c.MinimumMSSBodyRatio, "minimum_mss_displacement_atr": &c.MinimumMSSDisplacementATR,
		"minimum_mss_close_strength": &c.MinimumMSSCloseStrength,
		"entry_band_pips":            &c.EntryBandPips, "break_buffer_pips": &c.BreakBufferPips, "minimum_break_body_ratio": &c.MinimumBreakBodyRatio,
		"minimum_break_range_atr": &c.MinimumBreakRangeATR, "retest_tolerance_pips": &c.RetestTolerancePips, "displacement_grade_atr": &c.DisplacementGradeATR,
		"minimum_wick_ratio": &c.MinimumWickRatio, "stop_buffer_pips": &c.StopBufferPips, "stop_buffer_atr": &c.StopBufferATR,
		"minimum_target_r": &c.MinimumTargetR, "expiry_hours": &c.ExpiryHours,
		"session_momentum_minimum_atr": &c.SessionMomentumMinimumATR, "pip_size": &c.PipSize,
	}, map[string]*int{
		"atr_length": &c.ATRLength, "atr_window_bars": &c.ATRWindowBars, "reclaim_max_bars": &c.ReclaimMaxBars, "acceptance_closes": &c.AcceptanceCloses,
		"structure_pivot_bars": &c.StructurePivotBars, "structure_lookback_bars": &c.StructureLookbackBars, "mss_max_bars": &c.MSSMaxBars,
		"confirmation_max_age_bars": &c.ConfirmationMaxAgeBars, "retest_max_bars": &c.RetestMaxBars,
		"trading_day_rollover_utc_hour": &c.TradingDayRolloverHour,
	})
	if err != nil {
		return c, err
	}
	var ok bool
	if c.ConfirmationMode, ok = params["confirmation_mode"].(string); !ok {
		return c, fmt.Errorf("round_number: parameter %q must be a string", "confirmation_mode")
	}
	if c.Symbols, err = strategyutil.ParseSymbols(params); err != nil {
		return c, fmt.Errorf("round_number: %w", err)
	}
	return c, c.validate()
}

func (c Config) validate() error {
	switch {
	case c.HandleStep <= 0 || c.MajorStep < c.HandleStep:
		return fmt.Errorf("round_number: handle_step must be > 0 and major_step >= handle_step")
	case c.MaxHandleDistanceATR <= 0:
		return fmt.Errorf("round_number: max_handle_distance_atr must be > 0")
	case c.ATRLength < 2 || c.ATRWindowBars < c.ATRLength+1:
		return fmt.Errorf("round_number: atr_length must be >= 2 and atr_window_bars must exceed it")
	case c.MinimumSweepPips <= 0 || c.MaximumSweepPips < c.MinimumSweepPips:
		return fmt.Errorf("round_number: minimum_sweep_pips must be > 0 and maximum_sweep_pips >= it")
	case c.MinimumReclaimPips < 0 || c.ReclaimMaxBars < 0 || c.ReclaimMaxBars > 24 || c.AcceptanceCloses < 1:
		return fmt.Errorf("round_number: invalid reclaim window or acceptance_closes")
	case c.ConfirmationMode != "mss":
		return fmt.Errorf("round_number: confirmation_mode must be %q", "mss")
	case c.StructurePivotBars < 1 || c.StructurePivotBars > 5 || c.StructureLookbackBars < c.StructurePivotBars+1:
		return fmt.Errorf("round_number: invalid structure pivot or lookback")
	case c.MSSMaxBars < 0 || c.MSSMaxBars > 48 || c.ConfirmationMaxAgeBars < 0 || c.ConfirmationMaxAgeBars > 12:
		return fmt.Errorf("round_number: invalid mss_max_bars or confirmation_max_age_bars")
	case c.MinimumMSSBodyRatio < 0 || c.MinimumMSSBodyRatio > 1 || c.MinimumMSSCloseStrength < 0 || c.MinimumMSSCloseStrength > 1 || c.MinimumMSSDisplacementATR < 0:
		return fmt.Errorf("round_number: invalid MSS quality thresholds")
	case c.EntryBandPips <= 0 || c.BreakBufferPips < 0 || c.RetestTolerancePips < 0:
		return fmt.Errorf("round_number: entry_band_pips must be > 0 and the break/retest pips >= 0")
	case c.MinimumBreakBodyRatio < 0 || c.MinimumBreakBodyRatio > 1 || c.MinimumBreakRangeATR < 0 || c.DisplacementGradeATR < 0:
		return fmt.Errorf("round_number: invalid break thresholds")
	case c.RetestMaxBars < 1:
		return fmt.Errorf("round_number: retest_max_bars must be >= 1")
	case c.MinimumWickRatio < 0 || c.MinimumWickRatio > 1:
		return fmt.Errorf("round_number: minimum_wick_ratio must be in [0, 1]")
	case c.StopBufferPips < 0 || c.StopBufferATR < 0 || c.StopBufferPips == 0 && c.StopBufferATR == 0:
		return fmt.Errorf("round_number: a stop needs a positive buffer (pips or ATR)")
	case c.MinimumTargetR <= 0 || c.ExpiryHours <= 0 || c.PipSize <= 0 || c.SessionMomentumMinimumATR < 0:
		return fmt.Errorf("round_number: minimum_target_r, expiry_hours and pip_size must be > 0")
	case c.TradingDayRolloverHour < 0 || c.TradingDayRolloverHour > 23:
		return fmt.Errorf("round_number: trading_day_rollover_utc_hour must be in [0, 23]")
	}
	return nil
}
