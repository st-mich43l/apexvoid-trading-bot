package confluencezone

import (
	"fmt"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Config is the Confluence Zone v3 technical contract. Every coefficient is read
// from the strategy's parameters (config/analysis.yml); none has a code default,
// so a missing key fails construction. The values are shared v3 conventions (the
// same close-strength, wick and displacement coefficients iFVG v3 uses), not
// constants fitted to any trade; docs/strategies/confluence-zone-v3-technical-spec.md
// states what each one means and the replay certification shows how the result
// moves when they do.
type Config struct {
	// MinimumFacts is the number of independent source groups that must support
	// every price of the region.
	MinimumFacts int
	// A region must be strictly wider than max(MinimumOverlapPips x pip,
	// MinimumOverlapATR x ATR).
	MinimumOverlapPips float64
	MinimumOverlapATR  float64

	// Reaction: a touch and a confirming candle, both within ReactionLookback
	// closed candles of the evaluated one (the confirming candle at most
	// ReactionLookback-1 candles old).
	ReactionLookback int
	// A sweep of the region's far edge needs at least this depth.
	MinimumSweepPips float64
	MinimumSweepATR  float64
	// After a sweep the close must be back inside by at least this much ATR.
	MinimumReclaimATR float64
	// The rejecting wick, as a fraction of the candle's range, and where the
	// candle closes in its range (1 = at the extreme away from the region).
	RejectionWickRatio     float64
	RejectionCloseStrength float64
	// The smallest candle, in ATR, that can show a rejection.
	MinimumRejectionRangeATR float64
	// A displacement rejection (the candle closes through the region): body as a
	// fraction of range, and range in ATR. DisplacementGradeATR is the body that
	// earns the displacement factor.
	DisplacementBodyRatio float64
	DisplacementRangeATR  float64
	DisplacementGradeATR  float64

	// Geometry.
	InvalidationBufferATR float64
	MinimumTargetATR      float64
	MinimumRewardRisk     float64
	ExpiryHours           float64

	// Instrument facts the engine injects.
	PipSize            float64
	EntryMaxWidthPrice float64 // proximal slice of a wide region that may be entered; 0 = the whole region
	MaximumEntryATR    float64 // how far price may have run from the entry band
	// ExecutionStopMaxPips is the instrument's execution stop cap; a stop beyond it
	// is refused, never tightened. 0 disables the pre-check.
	ExecutionStopMaxPips float64
	// ExecutionStopMinPips is the instrument's execution stop floor: the execution
	// layer widens a tighter stop to it, so reward:risk is measured against at
	// least this much risk. 0 disables the adjustment.
	ExecutionStopMinPips float64
}

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	floats := map[string]*float64{
		"minimum_overlap_pips": &c.MinimumOverlapPips, "minimum_overlap_atr": &c.MinimumOverlapATR,
		"minimum_sweep_pips": &c.MinimumSweepPips, "minimum_sweep_atr": &c.MinimumSweepATR,
		"minimum_reclaim_atr": &c.MinimumReclaimATR, "rejection_wick_ratio": &c.RejectionWickRatio,
		"rejection_close_strength": &c.RejectionCloseStrength, "displacement_body_ratio": &c.DisplacementBodyRatio,
		"minimum_rejection_range_atr": &c.MinimumRejectionRangeATR, "displacement_range_atr": &c.DisplacementRangeATR, "displacement_grade_atr": &c.DisplacementGradeATR,
		"invalidation_buffer_atr": &c.InvalidationBufferATR, "minimum_target_distance_atr": &c.MinimumTargetATR,
		"minimum_reward_risk": &c.MinimumRewardRisk, "expiry_hours": &c.ExpiryHours, "pip_size": &c.PipSize,
		"maximum_entry_atr": &c.MaximumEntryATR,
	}
	// Sorted, so an error names the same key on every run.
	floatKeys := make([]string, 0, len(floats))
	for key := range floats {
		floatKeys = append(floatKeys, key)
	}
	sort.Strings(floatKeys)
	for _, key := range floatKeys {
		dst := floats[key]
		value, err := strategyutil.Float(params, key)
		if err != nil {
			return c, fmt.Errorf("confluence_zone: %w", err)
		}
		*dst = value
	}
	ints := map[string]*int{"minimum_facts": &c.MinimumFacts, "reaction_lookback_bars": &c.ReactionLookback}
	for _, key := range []string{"minimum_facts", "reaction_lookback_bars"} {
		dst := ints[key]
		value, err := strategyutil.Int(params, key)
		if err != nil {
			return c, fmt.Errorf("confluence_zone: %w", err)
		}
		*dst = value
	}
	if _, present := params["fvg_entry_max_width_price"]; present {
		v, err := strategyutil.Float(params, "fvg_entry_max_width_price")
		if err != nil {
			return c, fmt.Errorf("confluence_zone: %w", err)
		}
		c.EntryMaxWidthPrice = v
	}
	if _, present := params["execution_stop_max_pips"]; present {
		v, err := strategyutil.Float(params, "execution_stop_max_pips")
		if err != nil {
			return c, fmt.Errorf("confluence_zone: %w", err)
		}
		if !(v > 0) {
			return c, fmt.Errorf("confluence_zone: execution_stop_max_pips must be > 0 when set (omit it to disable the pre-check)")
		}
		c.ExecutionStopMaxPips = v
	}
	if _, present := params["execution_stop_min_pips"]; present {
		v, err := strategyutil.Float(params, "execution_stop_min_pips")
		if err != nil {
			return c, fmt.Errorf("confluence_zone: %w", err)
		}
		if v < 0 {
			return c, fmt.Errorf("confluence_zone: execution_stop_min_pips must be >= 0")
		}
		c.ExecutionStopMinPips = v
	}
	return c, c.validate()
}

func (c Config) validate() error {
	if c.MinimumFacts < 2 {
		return fmt.Errorf("confluence_zone: minimum_facts must be >= 2 (one structure is not a confluence)")
	}
	if c.ReactionLookback < 1 {
		return fmt.Errorf("confluence_zone: reaction_lookback_bars must be >= 1")
	}
	for name, v := range map[string]float64{
		"minimum_overlap_pips": c.MinimumOverlapPips, "minimum_overlap_atr": c.MinimumOverlapATR,
		"minimum_sweep_pips": c.MinimumSweepPips, "minimum_sweep_atr": c.MinimumSweepATR,
		"minimum_reclaim_atr": c.MinimumReclaimATR, "displacement_range_atr": c.DisplacementRangeATR,
		"displacement_grade_atr": c.DisplacementGradeATR, "minimum_reward_risk": c.MinimumRewardRisk, "minimum_rejection_range_atr": c.MinimumRejectionRangeATR,
		"fvg_entry_max_width_price": c.EntryMaxWidthPrice,
	} {
		if v < 0 {
			return fmt.Errorf("confluence_zone: %s must be >= 0", name)
		}
	}
	for name, v := range map[string]float64{
		"rejection_wick_ratio": c.RejectionWickRatio, "rejection_close_strength": c.RejectionCloseStrength, "displacement_body_ratio": c.DisplacementBodyRatio,
	} {
		if v < 0 || v > 1 {
			return fmt.Errorf("confluence_zone: %s must be within [0,1]", name)
		}
	}
	if c.PipSize <= 0 || c.ExpiryHours <= 0 || c.InvalidationBufferATR <= 0 || c.MinimumTargetATR <= 0 || c.MaximumEntryATR <= 0 {
		return fmt.Errorf("confluence_zone: pip_size, expiry_hours, invalidation_buffer_atr, minimum_target_distance_atr and maximum_entry_atr must be > 0")
	}
	return nil
}
