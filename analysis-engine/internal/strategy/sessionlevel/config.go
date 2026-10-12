package sessionlevel

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Config is the Session Level v3 technical contract. Every coefficient is read from
// the strategy's parameters (config/analysis.yml); none has a code default, so a
// missing key fails construction. The defaults are hypotheses validated against
// captures (docs/strategies/session_level.md), not claimed universal constants.
type Config struct {
	// Volatility and history.
	ATRLength       int
	ATRWindowBars   int
	PivotBars       int
	EpisodeLookback int // touches and sweeps older than this many M5 periods are not evaluated

	// UTC session grid, injected from analysis.sessions.
	AsiaStartHour, LondonStartHour, NYStartHour, RolloverHour int

	// Period completeness. A level exists only when the period that defines it is
	// fully covered by the candles.
	SessionStartToleranceMin int
	SessionEndToleranceMin   int
	SessionMaxGapMin         int
	SessionMinCoverage       float64
	PeriodStartToleranceMin  int
	PeriodEndToleranceMin    int
	PeriodMaxGapMin          int
	PeriodMinCoverage        float64
	SessionMaxAgeHours       float64
	DayMaxAgeHours           float64
	WeekMaxAgeHours          float64

	// Approach and first touch.
	TouchTolerancePips  float64
	TouchToleranceATR   float64
	ApproachLookback    int
	ApproachMinATR      float64
	TouchSeparationBars int
	MaximumPriorTouches int
	HoldBufferPips      float64
	HoldBufferATR       float64
	FailBufferPips      float64
	FailBufferATR       float64

	// Sweep and reclaim.
	SweepThresholdPips float64
	SweepThresholdATR  float64
	ReclaimBufferPips  float64
	ReclaimBufferATR   float64
	ReclaimMaxBars     int
	BreakoutAccept     int
	BreakoutBufferPips float64
	BreakoutBufferATR  float64

	// Rejection candle.
	RejectionCloseStrength float64
	RejectionWickRatio     float64
	RejectionBodyRatio     float64
	DisplacementGradeATR   float64
	ConfirmationWindow     int
	ConfirmationMaxAge     int
	MaxEpisodesPerLevel    int

	// Geometry.
	EntryBandATR          float64
	EntryTolerancePips    float64
	EntryToleranceATR     float64
	MaximumEntryDistATR   float64
	InvalidationBufferATR float64
	TargetLookbackBars    int
	TargetSwingBars       int
	TargetSwingATR        float64
	MinimumTargetRoomATR  float64
	MinimumRewardRisk     float64
	ExpiryHours           float64

	// Instrument facts the engine injects.
	PipSize              float64
	ExecutionStopMaxPips float64 // 0 disables the pre-check
}

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	floats := map[string]*float64{
		"session_minimum_coverage": &c.SessionMinCoverage, "period_minimum_coverage": &c.PeriodMinCoverage,
		"session_max_age_hours": &c.SessionMaxAgeHours, "day_max_age_hours": &c.DayMaxAgeHours, "week_max_age_hours": &c.WeekMaxAgeHours,
		"touch_tolerance_pips": &c.TouchTolerancePips, "touch_tolerance_atr": &c.TouchToleranceATR, "approach_min_distance_atr": &c.ApproachMinATR,
		"hold_buffer_pips": &c.HoldBufferPips, "hold_buffer_atr": &c.HoldBufferATR, "fail_buffer_pips": &c.FailBufferPips, "fail_buffer_atr": &c.FailBufferATR,
		"sweep_threshold_pips": &c.SweepThresholdPips, "sweep_threshold_atr": &c.SweepThresholdATR,
		"reclaim_buffer_pips": &c.ReclaimBufferPips, "reclaim_buffer_atr": &c.ReclaimBufferATR,
		"breakout_buffer_pips": &c.BreakoutBufferPips, "breakout_buffer_atr": &c.BreakoutBufferATR,
		"rejection_close_strength": &c.RejectionCloseStrength, "rejection_wick_ratio": &c.RejectionWickRatio, "rejection_body_ratio": &c.RejectionBodyRatio,
		"displacement_grade_atr": &c.DisplacementGradeATR,
		"entry_band_atr":         &c.EntryBandATR, "entry_tolerance_pips": &c.EntryTolerancePips, "entry_tolerance_atr": &c.EntryToleranceATR,
		"maximum_entry_distance_atr": &c.MaximumEntryDistATR, "invalidation_buffer_atr": &c.InvalidationBufferATR,
		"target_swing_atr": &c.TargetSwingATR, "minimum_target_room_atr": &c.MinimumTargetRoomATR, "minimum_reward_risk": &c.MinimumRewardRisk,
		"expiry_hours": &c.ExpiryHours, "pip_size": &c.PipSize,
	}
	for key, dst := range floats {
		value, err := strategyutil.Float(params, key)
		if err != nil {
			return c, fmt.Errorf("session_level: %w", err)
		}
		*dst = value
	}
	ints := map[string]*int{
		"atr_length": &c.ATRLength, "atr_window_bars": &c.ATRWindowBars, "pivot_bars": &c.PivotBars, "episode_lookback_bars": &c.EpisodeLookback,
		"asia_start_hour": &c.AsiaStartHour, "london_start_hour": &c.LondonStartHour, "ny_start_hour": &c.NYStartHour, "daily_rollover_utc_hour": &c.RolloverHour,
		"session_start_tolerance_minutes": &c.SessionStartToleranceMin, "session_end_tolerance_minutes": &c.SessionEndToleranceMin,
		"session_max_gap_minutes": &c.SessionMaxGapMin, "period_start_tolerance_minutes": &c.PeriodStartToleranceMin,
		"period_end_tolerance_minutes": &c.PeriodEndToleranceMin, "period_max_gap_minutes": &c.PeriodMaxGapMin,
		"approach_lookback_bars": &c.ApproachLookback, "touch_separation_bars": &c.TouchSeparationBars, "maximum_prior_touches": &c.MaximumPriorTouches,
		"reclaim_max_bars": &c.ReclaimMaxBars, "breakout_accept_closes": &c.BreakoutAccept,
		"confirmation_window_bars": &c.ConfirmationWindow, "confirmation_max_age_bars": &c.ConfirmationMaxAge, "max_episodes_per_level": &c.MaxEpisodesPerLevel,
		"target_lookback_bars": &c.TargetLookbackBars, "target_swing_bars": &c.TargetSwingBars,
	}
	for key, dst := range ints {
		value, err := strategyutil.Int(params, key)
		if err != nil {
			return c, fmt.Errorf("session_level: %w", err)
		}
		*dst = value
	}
	if _, present := params["execution_stop_max_pips"]; present {
		v, err := strategyutil.Float(params, "execution_stop_max_pips")
		if err != nil {
			return c, fmt.Errorf("session_level: %w", err)
		}
		if !(v > 0) {
			return c, fmt.Errorf("session_level: execution_stop_max_pips must be > 0 when set (omit it to disable the pre-check)")
		}
		c.ExecutionStopMaxPips = v
	}
	return c, c.validate()
}

func (c Config) validate() error {
	for name, v := range map[string]int{
		"atr_length": c.ATRLength, "pivot_bars": c.PivotBars, "episode_lookback_bars": c.EpisodeLookback, "approach_lookback_bars": c.ApproachLookback,
		"touch_separation_bars": c.TouchSeparationBars, "reclaim_max_bars": c.ReclaimMaxBars, "breakout_accept_closes": c.BreakoutAccept,
		"confirmation_window_bars": c.ConfirmationWindow, "max_episodes_per_level": c.MaxEpisodesPerLevel,
		"target_lookback_bars": c.TargetLookbackBars, "target_swing_bars": c.TargetSwingBars,
	} {
		if v < 1 {
			return fmt.Errorf("session_level: %s must be >= 1", name)
		}
	}
	if c.ATRWindowBars < c.ATRLength+1 {
		return fmt.Errorf("session_level: atr_window_bars must exceed atr_length")
	}
	if c.ConfirmationMaxAge < 0 || c.MaximumPriorTouches < 0 {
		return fmt.Errorf("session_level: confirmation_max_age_bars and maximum_prior_touches must be >= 0")
	}
	for _, h := range []int{c.AsiaStartHour, c.LondonStartHour, c.NYStartHour, c.RolloverHour} {
		if h < 0 || h > 23 {
			return fmt.Errorf("session_level: session hours must be within 0-23")
		}
	}
	if c.AsiaStartHour == c.LondonStartHour || c.LondonStartHour == c.NYStartHour || c.NYStartHour == c.AsiaStartHour {
		return fmt.Errorf("session_level: the three session start hours must differ")
	}
	for name, v := range map[string]int{
		"session_start_tolerance_minutes": c.SessionStartToleranceMin, "session_end_tolerance_minutes": c.SessionEndToleranceMin,
		"session_max_gap_minutes": c.SessionMaxGapMin, "period_start_tolerance_minutes": c.PeriodStartToleranceMin,
		"period_end_tolerance_minutes": c.PeriodEndToleranceMin, "period_max_gap_minutes": c.PeriodMaxGapMin,
	} {
		if v < 0 {
			return fmt.Errorf("session_level: %s must be >= 0", name)
		}
	}
	for name, v := range map[string]float64{
		"session_minimum_coverage": c.SessionMinCoverage, "period_minimum_coverage": c.PeriodMinCoverage,
		"rejection_close_strength": c.RejectionCloseStrength, "rejection_wick_ratio": c.RejectionWickRatio, "rejection_body_ratio": c.RejectionBodyRatio,
	} {
		if v < 0 || v > 1 {
			return fmt.Errorf("session_level: %s must be within [0,1]", name)
		}
	}
	for name, v := range map[string]float64{
		"session_max_age_hours": c.SessionMaxAgeHours, "day_max_age_hours": c.DayMaxAgeHours, "week_max_age_hours": c.WeekMaxAgeHours,
		"touch_tolerance_pips": c.TouchTolerancePips, "touch_tolerance_atr": c.TouchToleranceATR, "approach_min_distance_atr": c.ApproachMinATR,
		"hold_buffer_pips": c.HoldBufferPips, "hold_buffer_atr": c.HoldBufferATR, "fail_buffer_pips": c.FailBufferPips, "fail_buffer_atr": c.FailBufferATR,
		"sweep_threshold_pips": c.SweepThresholdPips, "sweep_threshold_atr": c.SweepThresholdATR, "reclaim_buffer_pips": c.ReclaimBufferPips,
		"reclaim_buffer_atr": c.ReclaimBufferATR, "breakout_buffer_pips": c.BreakoutBufferPips, "breakout_buffer_atr": c.BreakoutBufferATR,
		"entry_band_atr": c.EntryBandATR, "entry_tolerance_pips": c.EntryTolerancePips, "entry_tolerance_atr": c.EntryToleranceATR,
		"invalidation_buffer_atr": c.InvalidationBufferATR, "target_swing_atr": c.TargetSwingATR, "minimum_target_room_atr": c.MinimumTargetRoomATR,
		"minimum_reward_risk": c.MinimumRewardRisk, "displacement_grade_atr": c.DisplacementGradeATR,
	} {
		if v < 0 {
			return fmt.Errorf("session_level: %s must be >= 0", name)
		}
	}
	if c.PipSize <= 0 || c.ExpiryHours <= 0 || c.MaximumEntryDistATR <= 0 || c.EntryBandATR <= 0 {
		return fmt.Errorf("session_level: pip_size, expiry_hours, maximum_entry_distance_atr and entry_band_atr must be > 0")
	}
	return nil
}

// requiredHistory is how many M5 candles before the evaluated bar the detector
// reads: the longest session age plus its length, the episode lookback, the ATR
// window behind every decision and the target window. Slicing to exactly this
// makes a decision independent of how much older history is loaded.
func (c Config) requiredHistory() int {
	span := int(math.Max(c.SessionMaxAgeHours+9, c.DayMaxAgeHours)*12) + c.EpisodeLookback + c.ATRWindowBars
	if v := c.EpisodeLookback + c.TargetLookbackBars + c.TargetSwingBars + c.PivotBars; v > span {
		span = v
	}
	return span + 6
}

// requiredHigherHistory is how many H1 candles the day and week levels read: the
// previous week, the current week and the margin before them.
func (c Config) requiredHigherHistory() int {
	return int(c.WeekMaxAgeHours) + 9*24 + 6
}
