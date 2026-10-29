package asiarange

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Config is the Asia Range v1 technical contract. Every coefficient is read from the strategy's parameters (config/analysis.yml); none has a code
// default. pip_size and the stop-budget keys are per-instrument facts the engine injects.
type Config struct {
	ATRLength, ATRWindowBars int

	// The anchor: the finalized Asia range, qualified by its width in M15 ATR, live for a window from London open.
	MinimumRangeATR, MaximumRangeATR float64
	ActiveWindowHours                float64

	// Setup A: the London raid.
	MinimumSweepPips, MinimumSweepATR     float64
	MinimumReclaimPips, MinimumReclaimATR float64
	ReclaimMaxBars                        int
	StructurePivotBars                    int
	StructureLookbackBars                 int
	MSSMaxBars                            int
	MinimumMSSBodyRatio                   float64
	MinimumMSSDisplacementATR             float64
	MinimumMSSCloseStrength               float64
	ConfirmationMaxAgeBars                int

	// Setup B: the London acceptance.
	BreakBufferATR       float64 // M15 ATR units
	AcceptanceCloses     int
	RetestMaxBars        int
	DisplacementGradeATR float64 // M15 ATR units

	EntryBandPips         float64
	MinimumWickRatio      float64
	MeasuredMoveFraction  float64
	StopBufferPips        float64
	StopBufferATR         float64
	MinimumTargetR        float64
	ExpiryHours           float64
	SessionMomentumMinATR float64

	PipSize float64
	Symbols []string
}

// ParseConfig reads the Asia Range parameters; exported so replay and certification tooling configure exactly what production does.
func ParseConfig(params map[string]any) (Config, error) { return parseConfig(params) }

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	err := strategyutil.ParseNumbers("asia_range", params, map[string]*float64{
		"minimum_range_atr": &c.MinimumRangeATR, "maximum_range_atr": &c.MaximumRangeATR, "active_window_hours": &c.ActiveWindowHours,
		"minimum_sweep_pips": &c.MinimumSweepPips, "minimum_sweep_atr": &c.MinimumSweepATR,
		"minimum_reclaim_pips": &c.MinimumReclaimPips, "minimum_reclaim_atr": &c.MinimumReclaimATR,
		"minimum_mss_body_ratio": &c.MinimumMSSBodyRatio, "minimum_mss_displacement_atr": &c.MinimumMSSDisplacementATR,
		"minimum_mss_close_strength": &c.MinimumMSSCloseStrength,
		"break_buffer_atr":           &c.BreakBufferATR, "displacement_grade_atr": &c.DisplacementGradeATR,
		"entry_band_pips": &c.EntryBandPips, "minimum_wick_ratio": &c.MinimumWickRatio, "measured_move_fraction": &c.MeasuredMoveFraction,
		"stop_buffer_pips": &c.StopBufferPips, "stop_buffer_atr": &c.StopBufferATR, "minimum_target_r": &c.MinimumTargetR,
		"expiry_hours": &c.ExpiryHours, "session_momentum_minimum_atr": &c.SessionMomentumMinATR, "pip_size": &c.PipSize,
	}, map[string]*int{
		"atr_length": &c.ATRLength, "atr_window_bars": &c.ATRWindowBars, "reclaim_max_bars": &c.ReclaimMaxBars,
		"structure_pivot_bars": &c.StructurePivotBars, "structure_lookback_bars": &c.StructureLookbackBars, "mss_max_bars": &c.MSSMaxBars,
		"confirmation_max_age_bars": &c.ConfirmationMaxAgeBars, "acceptance_closes": &c.AcceptanceCloses, "retest_max_bars": &c.RetestMaxBars,
	})
	if err != nil {
		return c, err
	}
	if c.Symbols, err = strategyutil.ParseSymbols(params); err != nil {
		return c, fmt.Errorf("asia_range: %w", err)
	}
	return c, c.validate()
}

func (c Config) validate() error {
	switch {
	case c.ATRLength < 2 || c.ATRWindowBars < c.ATRLength+1:
		return fmt.Errorf("asia_range: atr_length must be >= 2 and atr_window_bars must exceed it")
	case c.MinimumRangeATR <= 0 || c.MaximumRangeATR <= c.MinimumRangeATR:
		return fmt.Errorf("asia_range: minimum_range_atr must be > 0 and maximum_range_atr above it")
	case c.ActiveWindowHours <= 0 || c.ActiveWindowHours > 12:
		return fmt.Errorf("asia_range: active_window_hours must be in (0, 12]")
	case c.MinimumSweepPips < 0 || c.MinimumSweepATR < 0 || c.MinimumSweepPips == 0 && c.MinimumSweepATR == 0:
		return fmt.Errorf("asia_range: a sweep needs a positive minimum penetration (pips or ATR)")
	case c.MinimumReclaimPips < 0 || c.MinimumReclaimATR < 0 || c.ReclaimMaxBars < 0 || c.ReclaimMaxBars > 24:
		return fmt.Errorf("asia_range: invalid reclaim thresholds")
	case c.StructurePivotBars < 1 || c.StructurePivotBars > 5 || c.StructureLookbackBars < c.StructurePivotBars+1:
		return fmt.Errorf("asia_range: invalid structure pivot or lookback")
	case c.MSSMaxBars < 0 || c.MSSMaxBars > 48 || c.ConfirmationMaxAgeBars < 0 || c.ConfirmationMaxAgeBars > 12:
		return fmt.Errorf("asia_range: invalid mss_max_bars or confirmation_max_age_bars")
	case c.MinimumMSSBodyRatio < 0 || c.MinimumMSSBodyRatio > 1 || c.MinimumMSSCloseStrength < 0 || c.MinimumMSSCloseStrength > 1 || c.MinimumMSSDisplacementATR < 0:
		return fmt.Errorf("asia_range: invalid MSS quality thresholds")
	case c.BreakBufferATR < 0 || c.AcceptanceCloses < 1 || c.RetestMaxBars < 1 || c.DisplacementGradeATR < 0:
		return fmt.Errorf("asia_range: invalid break thresholds")
	case c.EntryBandPips <= 0 || c.MinimumWickRatio < 0 || c.MinimumWickRatio > 1:
		return fmt.Errorf("asia_range: entry_band_pips must be > 0 and minimum_wick_ratio in [0, 1]")
	case c.MeasuredMoveFraction <= 0:
		return fmt.Errorf("asia_range: measured_move_fraction must be > 0")
	case c.StopBufferPips < 0 || c.StopBufferATR < 0 || c.StopBufferPips == 0 && c.StopBufferATR == 0:
		return fmt.Errorf("asia_range: a stop needs a positive buffer (pips or ATR)")
	case c.MinimumTargetR <= 0 || c.ExpiryHours <= 0 || c.PipSize <= 0 || c.SessionMomentumMinATR < 0:
		return fmt.Errorf("asia_range: minimum_target_r, expiry_hours and pip_size must be > 0")
	}
	return nil
}
