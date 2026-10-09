package crt

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Confirmation modes. "mss" is the CRT v3 contract; "sweep_reclaim" is the
// historical baseline (a reclaim alone confirms) kept ONLY so offline replays
// can measure the stronger rule against it. Production configuration selects
// the mode explicitly; there is no silent fallback between them.
const (
	ConfirmationMSS          = "mss"
	ConfirmationSweepReclaim = "sweep_reclaim"
)

// Entry models. mss_retest enters on the retest of the broken structure
// swing; reclaim_retest enters on the retest of the reclaimed H1 edge.
const (
	EntryMSSRetest     = "mss_retest"
	EntryReclaimRetest = "reclaim_retest"
)

// Config is the CRT v3 technical contract. Every coefficient is read from the
// strategy's parameters (config/analysis.yml); none has a code default, so a
// missing key fails construction instead of silently trading a guessed value.
// These are tunable parameters whose defaults are validated against captures
// (docs/strategies/crt.md), not claimed universal constants.
type Config struct {
	// Anchor.
	MinimumH1RangeATR    float64
	ATRLength            int
	ATRWindowBars        int
	SweepWindowH1Periods int

	// Sweep and reclaim. Thresholds are max(pips x pip size, ATR multiple).
	MinimumSweepPips   float64
	MinimumSweepATR    float64
	MinimumReclaimPips float64
	MinimumReclaimATR  float64
	ReclaimMaxBars     int

	// Confirmation.
	ConfirmationMode        string
	StructurePivotBars      int
	StructureLookbackBars   int
	MSSMaxBars              int
	MinimumMSSBodyRatio     float64
	MinimumMSSDisplacement  float64
	MinimumMSSCloseStrength float64
	DisplacementGradeATR    float64
	ConfirmationMaxAgeBars  int
	ConfirmationBufferPips  float64
	EntryModel              string
	EntryDepthATR           float64
	EntryToleranceATR       float64
	EntryMaxWidthPrice      float64
	InvalidationBufferATR   float64
	MinimumTargetRoomATR    float64
	MinimumRewardRisk       float64
	ExecutionStopMaxPips    float64
	ExpiryHours             float64
	PipSize                 float64
}

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	floats := map[string]*float64{
		"minimum_h1_range_atr":         &c.MinimumH1RangeATR,
		"minimum_sweep_pips":           &c.MinimumSweepPips,
		"minimum_sweep_atr":            &c.MinimumSweepATR,
		"minimum_reclaim_pips":         &c.MinimumReclaimPips,
		"minimum_reclaim_atr":          &c.MinimumReclaimATR,
		"minimum_mss_body_ratio":       &c.MinimumMSSBodyRatio,
		"minimum_mss_displacement_atr": &c.MinimumMSSDisplacement,
		"minimum_mss_close_strength":   &c.MinimumMSSCloseStrength,
		"displacement_grade_atr":       &c.DisplacementGradeATR,
		"confirmation_buffer_pips":     &c.ConfirmationBufferPips,
		"entry_depth_atr":              &c.EntryDepthATR,
		"entry_tolerance_atr":          &c.EntryToleranceATR,
		"entry_max_width_price":        &c.EntryMaxWidthPrice,
		"invalidation_buffer_atr":      &c.InvalidationBufferATR,
		"minimum_target_room_atr":      &c.MinimumTargetRoomATR,
		"minimum_reward_risk":          &c.MinimumRewardRisk,
		"expiry_hours":                 &c.ExpiryHours,
		"pip_size":                     &c.PipSize,
	}
	for key, dst := range floats {
		value, err := strategyutil.Float(params, key)
		if err != nil {
			return c, fmt.Errorf("crt: %w", err)
		}
		*dst = value
	}
	ints := map[string]*int{
		"atr_length":                &c.ATRLength,
		"atr_window_bars":           &c.ATRWindowBars,
		"sweep_window_h1_periods":   &c.SweepWindowH1Periods,
		"reclaim_max_bars":          &c.ReclaimMaxBars,
		"structure_pivot_bars":      &c.StructurePivotBars,
		"structure_lookback_bars":   &c.StructureLookbackBars,
		"mss_max_bars":              &c.MSSMaxBars,
		"confirmation_max_age_bars": &c.ConfirmationMaxAgeBars,
	}
	for key, dst := range ints {
		value, err := strategyutil.Int(params, key)
		if err != nil {
			return c, fmt.Errorf("crt: %w", err)
		}
		*dst = value
	}
	var ok bool
	if c.ConfirmationMode, ok = params["confirmation_mode"].(string); !ok {
		return c, fmt.Errorf("crt: parameter %q must be a string", "confirmation_mode")
	}
	if c.EntryModel, ok = params["entry_model"].(string); !ok {
		return c, fmt.Errorf("crt: parameter %q must be a string", "entry_model")
	}
	// The execution stop cap is a per-instrument fact the engine injects; an
	// absent value disables the pre-check (the Algo Bot still rejects an
	// over-envelope stop at execution).
	if _, present := params["execution_stop_max_pips"]; present {
		v, err := strategyutil.Float(params, "execution_stop_max_pips")
		if err != nil {
			return c, fmt.Errorf("crt: %w", err)
		}
		c.ExecutionStopMaxPips = v
	}
	return c, c.validate()
}

func (c Config) validate() error {
	switch {
	case c.MinimumH1RangeATR <= 0:
		return fmt.Errorf("crt: minimum_h1_range_atr must be > 0")
	case c.ATRLength < 2:
		return fmt.Errorf("crt: atr_length must be >= 2")
	case c.ATRWindowBars < c.ATRLength+1:
		return fmt.Errorf("crt: atr_window_bars must exceed atr_length")
	case c.SweepWindowH1Periods < 1 || c.SweepWindowH1Periods > 3:
		return fmt.Errorf("crt: sweep_window_h1_periods must be in [1, 3]")
	case c.MinimumSweepPips < 0 || c.MinimumSweepATR < 0 || c.MinimumSweepPips == 0 && c.MinimumSweepATR == 0:
		return fmt.Errorf("crt: a sweep needs a positive minimum penetration (pips or ATR)")
	case c.MinimumReclaimPips < 0 || c.MinimumReclaimATR < 0:
		return fmt.Errorf("crt: reclaim minimums must be >= 0")
	case c.ReclaimMaxBars < 0 || c.ReclaimMaxBars > 24:
		return fmt.Errorf("crt: reclaim_max_bars must be in [0, 24]")
	case c.ConfirmationMode != ConfirmationMSS && c.ConfirmationMode != ConfirmationSweepReclaim:
		return fmt.Errorf("crt: confirmation_mode must be %q or %q", ConfirmationMSS, ConfirmationSweepReclaim)
	case c.StructurePivotBars < 1 || c.StructurePivotBars > 5:
		return fmt.Errorf("crt: structure_pivot_bars must be in [1, 5]")
	case c.StructureLookbackBars < c.StructurePivotBars+1:
		return fmt.Errorf("crt: structure_lookback_bars must exceed structure_pivot_bars")
	case c.MSSMaxBars < 0 || c.MSSMaxBars > 48:
		return fmt.Errorf("crt: mss_max_bars must be in [0, 48]")
	case c.MinimumMSSBodyRatio < 0 || c.MinimumMSSBodyRatio > 1 || c.MinimumMSSCloseStrength < 0 || c.MinimumMSSCloseStrength > 1:
		return fmt.Errorf("crt: MSS body ratio and close strength must be in [0, 1]")
	case c.MinimumMSSDisplacement < 0 || c.DisplacementGradeATR <= 0 || c.ConfirmationBufferPips < 0:
		return fmt.Errorf("crt: MSS displacement, grade and buffer must be non-negative (grade > 0)")
	case c.ConfirmationMaxAgeBars < 0 || c.ConfirmationMaxAgeBars > 12:
		return fmt.Errorf("crt: confirmation_max_age_bars must be in [0, 12]")
	case c.EntryModel != EntryMSSRetest && c.EntryModel != EntryReclaimRetest:
		return fmt.Errorf("crt: entry_model must be %q or %q", EntryMSSRetest, EntryReclaimRetest)
	case c.EntryModel == EntryMSSRetest && c.ConfirmationMode != ConfirmationMSS:
		return fmt.Errorf("crt: entry_model %q needs confirmation_mode %q (there is no broken swing to retest otherwise)", EntryMSSRetest, ConfirmationMSS)
	case c.EntryDepthATR <= 0 || c.EntryToleranceATR < 0:
		return fmt.Errorf("crt: entry_depth_atr must be > 0 and entry_tolerance_atr >= 0")
	case c.EntryMaxWidthPrice <= 0:
		return fmt.Errorf("crt: entry_max_width_price must be > 0")
	case c.InvalidationBufferATR <= 0:
		return fmt.Errorf("crt: invalidation_buffer_atr must be > 0")
	case c.MinimumTargetRoomATR < 0 || c.MinimumRewardRisk <= 0:
		return fmt.Errorf("crt: minimum_target_room_atr must be >= 0 and minimum_reward_risk > 0")
	case c.ExecutionStopMaxPips < 0:
		return fmt.Errorf("crt: execution_stop_max_pips must be >= 0")
	case c.ExpiryHours <= 0:
		return fmt.Errorf("crt: expiry_hours must be > 0")
	case c.PipSize <= 0:
		return fmt.Errorf("crt: pip_size must be > 0")
	}
	return nil
}
