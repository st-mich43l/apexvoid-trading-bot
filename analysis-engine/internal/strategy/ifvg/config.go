package ifvg

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Config is the iFVG v3 technical contract. Every coefficient is read from the
// strategy's parameters (config/analysis.yml); none has a code default, so a
// missing key fails construction. The defaults are hypotheses validated against
// captures (docs/strategies/ifvg.md), not claimed universal constants.
type Config struct {
	// Volatility and history.
	ATRLength       int
	ATRWindowBars   int
	PivotBars       int // fractal width of the swing highs/lows the target is chosen from
	EpisodeLookback int // inversions older than this many M5 periods are not evaluated

	// The original fair-value gap.
	MinimumGapATR        float64
	MinimumGapPips       float64
	MinimumOriginBodyATR float64
	MinimumOriginBody    float64 // middle candle body / range
	MaximumInversionBars int     // the gap must invert within this many periods of forming

	// The inversion close.
	MinimumBreakPips      float64
	MinimumBreakATR       float64
	MinInversionBody      float64
	MinInversionCloseStr  float64
	MinInversionDisp      float64
	DisplacementGradeATR  float64
	RoleFlipFailPips      float64
	RoleFlipFailATR       float64
	RetestMaxBars         int
	RetestTouchPips       float64
	RetestTouchATR        float64
	ConfirmationWindow    int
	ConfirmationMaxAge    int
	RejectionCloseStr     float64
	RejectionWickRatio    float64
	RejectionBodyRatio    float64
	MaxRetestsPerInverse  int
	RetestDepartureATR    float64
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
	EntryMaxWidthPrice   float64 // proximal slice of the inverted gap that may be entered; 0 = whole gap
	ExecutionStopMaxPips float64 // 0 disables the pre-check
}

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	floats := map[string]*float64{
		"minimum_gap_atr": &c.MinimumGapATR, "minimum_gap_pips": &c.MinimumGapPips,
		"minimum_origin_body_atr": &c.MinimumOriginBodyATR, "minimum_origin_body_ratio": &c.MinimumOriginBody,
		"minimum_break_pips": &c.MinimumBreakPips, "minimum_break_atr": &c.MinimumBreakATR,
		"minimum_inversion_body_ratio": &c.MinInversionBody, "minimum_inversion_close_strength": &c.MinInversionCloseStr,
		"minimum_inversion_displacement_atr": &c.MinInversionDisp, "displacement_grade_atr": &c.DisplacementGradeATR,
		"role_flip_fail_pips": &c.RoleFlipFailPips, "role_flip_fail_atr": &c.RoleFlipFailATR,
		"retest_touch_pips": &c.RetestTouchPips, "retest_touch_atr": &c.RetestTouchATR,
		"rejection_close_strength": &c.RejectionCloseStr, "rejection_wick_ratio": &c.RejectionWickRatio,
		"rejection_body_ratio": &c.RejectionBodyRatio, "retest_departure_atr": &c.RetestDepartureATR,
		"entry_tolerance_pips": &c.EntryTolerancePips, "entry_tolerance_atr": &c.EntryToleranceATR,
		"maximum_entry_distance_atr": &c.MaximumEntryDistATR, "invalidation_buffer_atr": &c.InvalidationBufferATR,
		"target_swing_atr": &c.TargetSwingATR, "minimum_target_room_atr": &c.MinimumTargetRoomATR,
		"minimum_reward_risk": &c.MinimumRewardRisk, "expiry_hours": &c.ExpiryHours, "pip_size": &c.PipSize,
	}
	for key, dst := range floats {
		value, err := strategyutil.Float(params, key)
		if err != nil {
			return c, fmt.Errorf("ifvg: %w", err)
		}
		*dst = value
	}
	ints := map[string]*int{
		"atr_length": &c.ATRLength, "atr_window_bars": &c.ATRWindowBars, "pivot_bars": &c.PivotBars, "episode_lookback_bars": &c.EpisodeLookback,
		"maximum_inversion_bars": &c.MaximumInversionBars, "retest_max_bars": &c.RetestMaxBars,
		"confirmation_window_bars": &c.ConfirmationWindow, "confirmation_max_age_bars": &c.ConfirmationMaxAge,
		"max_retests_per_inversion": &c.MaxRetestsPerInverse, "target_lookback_bars": &c.TargetLookbackBars,
		"target_swing_bars": &c.TargetSwingBars,
	}
	for key, dst := range ints {
		value, err := strategyutil.Int(params, key)
		if err != nil {
			return c, fmt.Errorf("ifvg: %w", err)
		}
		*dst = value
	}
	// The proximal-slice width is the instrument's entry cap (the same leaf the
	// engine injects for every clipped technique); absent means the whole gap.
	if _, present := params["fvg_entry_max_width_price"]; present {
		v, err := strategyutil.Float(params, "fvg_entry_max_width_price")
		if err != nil {
			return c, fmt.Errorf("ifvg: %w", err)
		}
		c.EntryMaxWidthPrice = v
	}
	// The execution stop cap is a per-instrument fact the engine injects; absent
	// disables the pre-check (the Algo Bot still rejects an over-envelope stop).
	if _, present := params["execution_stop_max_pips"]; present {
		v, err := strategyutil.Float(params, "execution_stop_max_pips")
		if err != nil {
			return c, fmt.Errorf("ifvg: %w", err)
		}
		if !(v > 0) {
			return c, fmt.Errorf("ifvg: execution_stop_max_pips must be > 0 when set (omit it to disable the pre-check)")
		}
		c.ExecutionStopMaxPips = v
	}
	return c, c.validate()
}

func (c Config) validate() error {
	for name, v := range map[string]int{
		"atr_length": c.ATRLength, "pivot_bars": c.PivotBars, "episode_lookback_bars": c.EpisodeLookback, "maximum_inversion_bars": c.MaximumInversionBars,
		"retest_max_bars": c.RetestMaxBars, "confirmation_window_bars": c.ConfirmationWindow,
		"max_retests_per_inversion": c.MaxRetestsPerInverse, "target_lookback_bars": c.TargetLookbackBars,
		"target_swing_bars": c.TargetSwingBars,
	} {
		if v < 1 {
			return fmt.Errorf("ifvg: %s must be >= 1", name)
		}
	}
	if c.ATRWindowBars < c.ATRLength+1 {
		return fmt.Errorf("ifvg: atr_window_bars must exceed atr_length")
	}
	if c.ConfirmationMaxAge < 0 {
		return fmt.Errorf("ifvg: confirmation_max_age_bars must be >= 0")
	}
	for name, v := range map[string]float64{
		"minimum_gap_atr": c.MinimumGapATR, "minimum_gap_pips": c.MinimumGapPips, "minimum_origin_body_atr": c.MinimumOriginBodyATR,
		"minimum_break_pips": c.MinimumBreakPips, "minimum_break_atr": c.MinimumBreakATR, "minimum_inversion_displacement_atr": c.MinInversionDisp,
		"role_flip_fail_pips": c.RoleFlipFailPips, "role_flip_fail_atr": c.RoleFlipFailATR, "retest_touch_pips": c.RetestTouchPips,
		"retest_touch_atr": c.RetestTouchATR, "retest_departure_atr": c.RetestDepartureATR, "entry_tolerance_pips": c.EntryTolerancePips,
		"entry_tolerance_atr": c.EntryToleranceATR, "invalidation_buffer_atr": c.InvalidationBufferATR, "target_swing_atr": c.TargetSwingATR,
		"minimum_target_room_atr": c.MinimumTargetRoomATR, "minimum_reward_risk": c.MinimumRewardRisk,
		"displacement_grade_atr": c.DisplacementGradeATR, "fvg_entry_max_width_price": c.EntryMaxWidthPrice,
	} {
		if v < 0 {
			return fmt.Errorf("ifvg: %s must be >= 0", name)
		}
	}
	for name, v := range map[string]float64{
		"minimum_origin_body_ratio": c.MinimumOriginBody, "minimum_inversion_body_ratio": c.MinInversionBody,
		"minimum_inversion_close_strength": c.MinInversionCloseStr, "rejection_close_strength": c.RejectionCloseStr,
		"rejection_wick_ratio": c.RejectionWickRatio, "rejection_body_ratio": c.RejectionBodyRatio,
	} {
		if v < 0 || v > 1 {
			return fmt.Errorf("ifvg: %s must be within [0,1]", name)
		}
	}
	if c.PipSize <= 0 || c.ExpiryHours <= 0 || c.MaximumEntryDistATR <= 0 {
		return fmt.Errorf("ifvg: pip_size, expiry_hours and maximum_entry_distance_atr must be > 0")
	}
	return nil
}

// requiredHistory is how many candles before the evaluated bar the detector reads:
// the episode lookback, the age a gap may have when it inverts, and the ATR and
// target windows behind every decision. Slicing to exactly this makes a decision
// independent of how much older history happens to be loaded.
func (c Config) requiredHistory() int {
	span := c.EpisodeLookback + c.MaximumInversionBars + c.ATRWindowBars
	if v := c.EpisodeLookback + c.TargetLookbackBars + c.TargetSwingBars + c.MaximumInversionBars; v > span {
		span = v
	}
	return span + 6
}
