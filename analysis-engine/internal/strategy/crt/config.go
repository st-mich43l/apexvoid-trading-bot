package crt

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
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
// swing; reclaim_retest enters on the retest of the reclaimed anchor edge (both v3,
// kept selectable: with structure_timeframes [H1] they are v3 exactly). reclaim_leg (v4,
// the default) enters on the reclaim leg itself, from the swept edge to the
// structure-shift close, and carries the v4 contract: the opposite edge as the
// objective judged on the effective risk, the anchor range published as the
// structure (candle 2's close rule) and the per-frame expiry.
const (
	EntryMSSRetest     = "mss_retest"
	EntryReclaimRetest = "reclaim_retest"
	EntryReclaimLeg    = "reclaim_leg"
)

// anchorTimeframes are the frames a CRT anchor may be read on (owner rule 23 Nov 2026: setups are found on M5, M15 and H1, M1 is execution only). M5 is not
// one of them: an M5 sweep cannot confirm an M5 range.
var anchorTimeframes = []market.Timeframe{market.M15, market.H1}

// Config is the CRT v3 technical contract. Every coefficient is read from the
// strategy's parameters (config/analysis.yml); none has a code default, so a
// missing key fails construction instead of silently trading a guessed value.
// These are tunable parameters whose defaults are validated against captures
// (docs/strategies/crt.md), not claimed universal constants.
type Config struct {
	// Anchor. MinimumRangeATR is in ATRs of the anchor's own frame (minimum_range_atr; minimum_h1_range_atr is read as an alias for one release);
	// SweepWindowPeriods counts periods of the anchor's frame (sweep_window_periods, alias sweep_window_h1_periods).
	MinimumRangeATR     float64
	ATRLength           int
	ATRWindowBars       int
	SweepWindowPeriods  int
	StructureTimeframes []market.Timeframe

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
	// ExecutionStopMinPips is the instrument's execution stop floor: execution widens a tighter stop to it. The quality score reads it to measure room against
	// the risk the trade is actually exposed to; the detector decides on the structural stop and the cap alone. 0 = no floor known.
	ExecutionStopMinPips float64
	ExpiryHours          float64
	PipSize              float64

	// v4 (entry_model reclaim_leg). ExpiryAfterCandle2Periods caps the expiry at this many periods of the anchor frame after candle 2 closes;
	// StopBudget is the instrument's stop envelope (cap, floor, deep-entry minimum width) the reclaim leg is fitted with; EntryZoneMinimumWidthPips is the
	// instrument's narrowest entry zone (stop_envelope.min_entry_zone_width_pips), refused by CRT itself before the engine would.
	ExpiryAfterCandle2Periods map[market.Timeframe]int
	StructureCloseBufferATR   float64
	StopBudget                strategyutil.StopBudget
	EntryZoneMinimumWidthPips float64
}

// v4 reports whether the v4 contract applies (entry_model reclaim_leg).
func (c Config) v4() bool { return c.EntryModel == EntryReclaimLeg }

// budgetPip is the pip the stop budget and the zone width are measured in: the instrument's execution pip when injected, else the strategy's own.
func (c Config) budgetPip() float64 {
	if c.StopBudget.PipSize > 0 {
		return c.StopBudget.PipSize
	}
	return c.PipSize
}

func parseConfig(raw map[string]any) (Config, error) {
	var c Config
	params := make(map[string]any, len(raw)) // the aliases below must never change the caller's parameters (they are fingerprinted)
	for k, v := range raw {
		params[k] = v
	}
	if err := aliasKey(params, "minimum_range_atr", "minimum_h1_range_atr"); err != nil {
		return c, err
	}
	if err := aliasKey(params, "sweep_window_periods", "sweep_window_h1_periods"); err != nil {
		return c, err
	}
	floats := map[string]*float64{
		"minimum_range_atr":            &c.MinimumRangeATR,
		"minimum_sweep_pips":           &c.MinimumSweepPips,
		"minimum_sweep_atr":            &c.MinimumSweepATR,
		"minimum_reclaim_pips":         &c.MinimumReclaimPips,
		"minimum_reclaim_atr":          &c.MinimumReclaimATR,
		"minimum_mss_body_ratio":       &c.MinimumMSSBodyRatio,
		"minimum_mss_displacement_atr": &c.MinimumMSSDisplacement,
		"minimum_mss_close_strength":   &c.MinimumMSSCloseStrength,
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
		"sweep_window_periods":      &c.SweepWindowPeriods,
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
		if !(v > 0) {
			return c, fmt.Errorf("crt: execution_stop_max_pips must be > 0 when set (omit it to disable the pre-check)")
		}
		c.ExecutionStopMaxPips = v
	}
	if _, present := params["execution_stop_min_pips"]; present {
		v, err := strategyutil.Float(params, "execution_stop_min_pips")
		if err != nil {
			return c, fmt.Errorf("crt: %w", err)
		}
		if v < 0 {
			return c, fmt.Errorf("crt: execution_stop_min_pips must be >= 0")
		}
		c.ExecutionStopMinPips = v
	}
	// Anchor frames (v4). Absent means H1 only, the v3 anchor.
	c.StructureTimeframes = []market.Timeframe{market.H1}
	if _, present := params[strategyutil.StructureTimeframesKey]; present {
		tfs, err := strategyutil.ParseStructureTimeframes(params, anchorTimeframes)
		if err != nil {
			return c, fmt.Errorf("crt: %w", err)
		}
		c.StructureTimeframes = tfs
	}
	if _, present := params["structure_close_buffer_atr"]; present {
		v, err := strategyutil.Float(params, "structure_close_buffer_atr")
		if err != nil || v < 0 || math.IsNaN(v) || math.IsInf(v, 0) {
			return c, fmt.Errorf("crt: structure_close_buffer_atr must be a finite number >= 0")
		}
		c.StructureCloseBufferATR = v
	}
	if raw, present := params["expiry_after_candle2_periods"]; present {
		periods, err := parsePeriods(raw)
		if err != nil {
			return c, err
		}
		c.ExpiryAfterCandle2Periods = periods
	}
	budget, err := strategyutil.ParseStopBudget(params)
	if err != nil {
		return c, fmt.Errorf("crt: %w", err)
	}
	c.StopBudget = budget
	if _, present := params["entry_zone_minimum_width_pips"]; present {
		v, err := strategyutil.Float(params, "entry_zone_minimum_width_pips")
		if err != nil || v < 0 {
			return c, fmt.Errorf("crt: entry_zone_minimum_width_pips must be >= 0")
		}
		c.EntryZoneMinimumWidthPips = v
	}
	return c, c.validate()
}

// aliasKey copies the deprecated key's value to the current one when only the deprecated one is set; both set to different values is an error.
func aliasKey(params map[string]any, current, deprecated string) error {
	old, hasOld := params[deprecated]
	cur, hasCur := params[current]
	switch {
	case hasOld && hasCur && fmt.Sprint(old) != fmt.Sprint(cur):
		return fmt.Errorf("crt: %q and its deprecated alias %q disagree (%v vs %v)", current, deprecated, cur, old)
	case hasOld && !hasCur:
		params[current] = old
	}
	return nil
}

func parsePeriods(raw any) (map[market.Timeframe]int, error) {
	m, ok := raw.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("crt: expiry_after_candle2_periods must be a mapping of timeframe to periods, got %T", raw)
	}
	out := map[market.Timeframe]int{}
	for k, v := range m {
		tf := market.Timeframe(k)
		if _, known := tf.Minutes(); !known {
			return nil, fmt.Errorf("crt: expiry_after_candle2_periods: %q is not a timeframe", k)
		}
		f, ok := v.(float64)
		if i, isInt := v.(int); isInt {
			f, ok = float64(i), true
		}
		if !ok || f < 1 || f != math.Trunc(f) {
			return nil, fmt.Errorf("crt: expiry_after_candle2_periods.%s must be a whole number >= 1", k)
		}
		out[tf] = int(f)
	}
	return out, nil
}

func (c Config) validate() error {
	switch {
	case c.MinimumRangeATR <= 0:
		return fmt.Errorf("crt: minimum_range_atr must be > 0")
	case c.ATRLength < 2:
		return fmt.Errorf("crt: atr_length must be >= 2")
	case c.ATRWindowBars < c.ATRLength+1:
		return fmt.Errorf("crt: atr_window_bars must exceed atr_length")
	case c.SweepWindowPeriods < 1 || c.SweepWindowPeriods > 3:
		return fmt.Errorf("crt: sweep_window_periods must be in [1, 3]")
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
	case c.MinimumMSSDisplacement < 0 || c.ConfirmationBufferPips < 0:
		return fmt.Errorf("crt: MSS displacement and buffer must be non-negative")
	case c.ConfirmationMaxAgeBars < 0 || c.ConfirmationMaxAgeBars > 12:
		return fmt.Errorf("crt: confirmation_max_age_bars must be in [0, 12]")
	case c.EntryModel != EntryMSSRetest && c.EntryModel != EntryReclaimRetest && c.EntryModel != EntryReclaimLeg:
		return fmt.Errorf("crt: entry_model must be %q, %q or %q", EntryReclaimLeg, EntryMSSRetest, EntryReclaimRetest)
	case c.EntryModel == EntryReclaimLeg && c.ConfirmationMode != ConfirmationMSS:
		return fmt.Errorf("crt: entry_model %q needs confirmation_mode %q (the leg ends at the structure-shift close)", EntryReclaimLeg, ConfirmationMSS)
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
