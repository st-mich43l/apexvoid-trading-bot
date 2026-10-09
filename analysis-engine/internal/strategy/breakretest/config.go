package breakretest

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Protected-structure models: the structure whose loss means the break thesis
// failed. last_pivot is the most recent confirmed pivot on the pre-break side;
// break_origin is the lowest point of the launch (the bars from the pre-break
// window through the acceptance bar).
const (
	ProtectedLastPivot   = "last_pivot"
	ProtectedBreakOrigin = "break_origin"
)

// Config is the Break & Retest v3 technical contract. Every coefficient is
// read from the strategy's parameters (config/analysis.yml); none has a code
// default, so a missing key fails construction. The defaults are validated
// against captures (docs/strategies/break_retest.md), not claimed universal.
type Config struct {
	// Volatility and history.
	ATRLength     int
	ATRWindowBars int

	// Reference structures.
	PivotBars         int
	ReferenceLookback int
	EpisodeLookback   int
	LevelMinTouches   int
	LevelClusterPips  float64
	LevelClusterATR   float64
	LevelSpanMultiple float64
	LineMinSpanBars   int
	LineMinSlopeATR   float64
	LineMaxSlopeATR   float64
	LineWickATR       float64
	LineMaxGapRatio   float64
	PreBreakBars      int

	// Breakout acceptance.
	BreakoutAcceptBars    int
	BreakBufferPips       float64
	BreakBufferATR        float64
	MinBreakBodyRatio     float64
	MinBreakCloseStrength float64
	MinBreakDisplacement  float64
	DisplacementGradeATR  float64
	BreakFailPips         float64
	BreakFailATR          float64

	// Retest.
	RetestMaxBars          int
	RetestTouchPips        float64
	RetestTouchATR         float64
	ConfirmationWindowBars int
	ConfirmationMaxAgeBars int
	RejectionCloseStrength float64
	RejectionWickRatio     float64
	RejectionBodyRatio     float64

	// Geometry.
	ProtectedStructure      string
	EntryTolerancePips      float64
	EntryToleranceATR       float64
	MaximumEntryDistanceATR float64
	InvalidationBufferATR   float64
	TargetLookbackBars      int
	TargetSwingBars         int
	TargetSwingATR          float64
	MinimumTargetRoomATR    float64
	MinimumRewardRisk       float64
	ExecutionStopMaxPips    float64 // 0 disables the pre-check
	ExpiryHours             float64
	PipSize                 float64

	// PublishedOverallQuality is Quality.Overall exactly as live arbitration has
	// always seen it for this strategy (0.75). The measured quality of each
	// setup is published in Quality.Components only; moving Overall onto a
	// measured score is an arbitration decision this strategy does not take.
	PublishedOverallQuality float64
}

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	floats := map[string]*float64{
		"level_cluster_pips": &c.LevelClusterPips, "level_cluster_atr": &c.LevelClusterATR, "level_span_multiple": &c.LevelSpanMultiple,
		"line_min_slope_atr": &c.LineMinSlopeATR, "line_max_slope_atr": &c.LineMaxSlopeATR, "line_wick_atr": &c.LineWickATR,
		"line_max_gap_ratio": &c.LineMaxGapRatio,
		"break_buffer_pips":  &c.BreakBufferPips, "break_buffer_atr": &c.BreakBufferATR,
		"minimum_break_body_ratio": &c.MinBreakBodyRatio, "minimum_break_close_strength": &c.MinBreakCloseStrength,
		"minimum_break_displacement_atr": &c.MinBreakDisplacement, "displacement_grade_atr": &c.DisplacementGradeATR,
		"break_fail_pips": &c.BreakFailPips, "break_fail_atr": &c.BreakFailATR,
		"retest_touch_pips": &c.RetestTouchPips, "retest_touch_atr": &c.RetestTouchATR,
		"rejection_close_strength": &c.RejectionCloseStrength, "rejection_wick_ratio": &c.RejectionWickRatio, "rejection_body_ratio": &c.RejectionBodyRatio,
		"entry_tolerance_pips": &c.EntryTolerancePips, "entry_tolerance_atr": &c.EntryToleranceATR,
		"maximum_entry_distance_atr": &c.MaximumEntryDistanceATR, "invalidation_buffer_atr": &c.InvalidationBufferATR,
		"target_swing_atr": &c.TargetSwingATR, "minimum_target_room_atr": &c.MinimumTargetRoomATR, "minimum_reward_risk": &c.MinimumRewardRisk,
		"expiry_hours": &c.ExpiryHours, "pip_size": &c.PipSize, "published_overall_quality": &c.PublishedOverallQuality,
	}
	for key, dst := range floats {
		value, err := strategyutil.Float(params, key)
		if err != nil {
			return c, fmt.Errorf("break_retest: %w", err)
		}
		*dst = value
	}
	ints := map[string]*int{
		"atr_length": &c.ATRLength, "atr_window_bars": &c.ATRWindowBars, "pivot_bars": &c.PivotBars,
		"reference_lookback_bars": &c.ReferenceLookback, "episode_lookback_bars": &c.EpisodeLookback,
		"level_min_touches": &c.LevelMinTouches, "line_min_span_bars": &c.LineMinSpanBars, "pre_break_bars": &c.PreBreakBars,
		"breakout_accept_bars": &c.BreakoutAcceptBars, "retest_max_bars": &c.RetestMaxBars,
		"confirmation_window_bars": &c.ConfirmationWindowBars, "confirmation_max_age_bars": &c.ConfirmationMaxAgeBars,
		"target_lookback_bars": &c.TargetLookbackBars, "target_swing_bars": &c.TargetSwingBars,
	}
	for key, dst := range ints {
		value, err := strategyutil.Int(params, key)
		if err != nil {
			return c, fmt.Errorf("break_retest: %w", err)
		}
		*dst = value
	}
	var ok bool
	if c.ProtectedStructure, ok = params["protected_structure"].(string); !ok {
		return c, fmt.Errorf("break_retest: parameter %q must be a string", "protected_structure")
	}
	// The execution stop cap is a per-instrument fact the engine injects; an
	// absent value disables the pre-check (the Algo Bot still rejects an
	// over-envelope stop at execution).
	if _, present := params["execution_stop_max_pips"]; present {
		v, err := strategyutil.Float(params, "execution_stop_max_pips")
		if err != nil {
			return c, fmt.Errorf("break_retest: %w", err)
		}
		if !(v > 0) {
			return c, fmt.Errorf("break_retest: execution_stop_max_pips must be > 0 when set (omit it to disable the pre-check)")
		}
		c.ExecutionStopMaxPips = v
	}
	return c, c.validate()
}

func (c Config) validate() error {
	positiveInts := map[string]int{
		"atr_length": c.ATRLength, "pivot_bars": c.PivotBars, "reference_lookback_bars": c.ReferenceLookback,
		"episode_lookback_bars": c.EpisodeLookback, "level_min_touches": c.LevelMinTouches, "line_min_span_bars": c.LineMinSpanBars,
		"pre_break_bars": c.PreBreakBars, "breakout_accept_bars": c.BreakoutAcceptBars, "retest_max_bars": c.RetestMaxBars,
		"confirmation_window_bars": c.ConfirmationWindowBars, "target_lookback_bars": c.TargetLookbackBars, "target_swing_bars": c.TargetSwingBars,
	}
	for name, v := range positiveInts {
		if v < 1 {
			return fmt.Errorf("break_retest: %s must be >= 1", name)
		}
	}
	if c.LevelMinTouches < 2 {
		return fmt.Errorf("break_retest: level_min_touches must be >= 2: a level needs at least two touches")
	}
	if c.ATRWindowBars < c.ATRLength+1 {
		return fmt.Errorf("break_retest: atr_window_bars must exceed atr_length")
	}
	if c.ConfirmationMaxAgeBars < 0 {
		return fmt.Errorf("break_retest: confirmation_max_age_bars must be >= 0")
	}
	nonNegative := map[string]float64{
		"level_cluster_pips": c.LevelClusterPips, "level_cluster_atr": c.LevelClusterATR, "line_min_slope_atr": c.LineMinSlopeATR,
		"line_wick_atr": c.LineWickATR, "break_buffer_pips": c.BreakBufferPips, "break_buffer_atr": c.BreakBufferATR,
		"minimum_break_displacement_atr": c.MinBreakDisplacement, "break_fail_pips": c.BreakFailPips, "break_fail_atr": c.BreakFailATR,
		"retest_touch_pips": c.RetestTouchPips, "retest_touch_atr": c.RetestTouchATR, "entry_tolerance_pips": c.EntryTolerancePips,
		"entry_tolerance_atr": c.EntryToleranceATR, "invalidation_buffer_atr": c.InvalidationBufferATR, "target_swing_atr": c.TargetSwingATR,
		"minimum_target_room_atr": c.MinimumTargetRoomATR, "minimum_reward_risk": c.MinimumRewardRisk, "execution_stop_max_pips": c.ExecutionStopMaxPips,
	}
	for name, v := range nonNegative {
		if v < 0 {
			return fmt.Errorf("break_retest: %s must be >= 0", name)
		}
	}
	for name, v := range map[string]float64{
		"minimum_break_body_ratio": c.MinBreakBodyRatio, "minimum_break_close_strength": c.MinBreakCloseStrength,
		"rejection_close_strength": c.RejectionCloseStrength, "rejection_wick_ratio": c.RejectionWickRatio,
		"rejection_body_ratio": c.RejectionBodyRatio, "published_overall_quality": c.PublishedOverallQuality,
	} {
		if v < 0 || v > 1 {
			return fmt.Errorf("break_retest: %s must be within [0,1]", name)
		}
	}
	if c.PipSize <= 0 || c.ExpiryHours <= 0 || c.LevelSpanMultiple < 1 || c.LineMaxSlopeATR <= c.LineMinSlopeATR || c.LineMaxGapRatio < 1 || c.MaximumEntryDistanceATR <= 0 {
		return fmt.Errorf("break_retest: invalid parameters")
	}
	switch c.ProtectedStructure {
	case ProtectedLastPivot, ProtectedBreakOrigin:
	default:
		return fmt.Errorf("break_retest: protected_structure %q must be %s or %s", c.ProtectedStructure, ProtectedLastPivot, ProtectedBreakOrigin)
	}
	return nil
}

// requiredHistory is how many candles before the evaluated bar the detector
// reads. Slicing to exactly this makes a decision independent of how much older
// history happens to be loaded.
func (c Config) requiredHistory() int {
	span := c.EpisodeLookback + c.ReferenceLookback + c.PivotBars
	if v := c.EpisodeLookback + c.ATRWindowBars; v > span {
		span = v
	}
	if v := c.EpisodeLookback + c.TargetLookbackBars + c.TargetSwingBars + c.PivotBars; v > span {
		span = v
	}
	return span + c.PivotBars + 2
}
