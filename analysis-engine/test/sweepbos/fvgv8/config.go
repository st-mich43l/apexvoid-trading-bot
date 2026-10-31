package fvgv8

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Config is the fvg strategy's own parsed configuration (analysis.strategies.fvg): every value the strategy reads and nothing it does not. Missing keys fail
// startup. The engine injects only the instrument scale (pip_size, entry_max_width_price, the stop cap) and the shared confluence rubric.
type Config struct {
	// StructureTimeframes are the frames a gap is found on. M5 only confirms and executes.
	StructureTimeframes []market.Timeframe

	// The gap definition, judged on the gap's own frame as of its third candle.
	MinimumGapATR                float64
	MinimumDisplacementBodyATR   float64
	MinimumDisplacementBodyRatio float64
	// CloseThroughBufferATR retires a gap once a close of its frame goes beyond its far edge by more than this share of that bar's ATR.
	CloseThroughBufferATR float64
	// MaximumZoneATR is the widest gap, in ATR of its frame, that is a gap and not a region.
	MaximumZoneATR float64
	// EntryMaxWidthPrice clips a wide gap to its proximal slice (instrument scale, injected per instrument).
	EntryMaxWidthPrice float64
	MaximumEntryATR    float64

	// Confirmation (M5): a touch, then within ConfirmationWindowBars a rejection, not older than ConfirmationMaxAgeBars at the decision bar.
	ConfirmationWindowBars   int
	ConfirmationMaxAgeBars   int
	MinimumWickRatio         float64
	EngulfingMinimumRangeATR float64

	// Stop, target, room, extension.
	InvalidationBufferATR  float64
	MinimumTargetR         float64
	FallbackTargetR        float64
	ObstaclePivotBars      int
	LegLookbackBars        int
	ExtensionMinimumLegATR float64
	MinimumLegRetracement  float64

	// Qualification.
	ConfluenceFloor     int
	FibonacciEnabled    bool
	FibonacciEpsilonATR float64
	Confluence          confluence.Config
	ExpiryHours         float64
	PipSize             float64

	// BindWithinExecutionWindow drops a structure whose origin precedes the M5 execution window, as the frozen analysis did. Optional, false in production;
	// only the frozen-Python parity replays set it (engine.WithFrozenWindows).
	BindWithinExecutionWindow bool

	// StopBudget is the instrument's stop envelope (strategyutil.StopBudget); the zero value is no budget.
	StopBudget strategyutil.StopBudget
}

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	floats := map[string]*float64{
		"minimum_gap_atr": &c.MinimumGapATR, "minimum_displacement_body_atr": &c.MinimumDisplacementBodyATR, "minimum_displacement_body_ratio": &c.MinimumDisplacementBodyRatio,
		"close_through_buffer_atr": &c.CloseThroughBufferATR, "maximum_zone_atr": &c.MaximumZoneATR,
		"entry_max_width_price": &c.EntryMaxWidthPrice, "maximum_entry_atr": &c.MaximumEntryATR,
		"minimum_wick_ratio": &c.MinimumWickRatio, "engulfing_minimum_range_atr": &c.EngulfingMinimumRangeATR,
		"invalidation_buffer_atr": &c.InvalidationBufferATR, "minimum_target_r": &c.MinimumTargetR, "fallback_target_r": &c.FallbackTargetR,
		"extension_minimum_leg_atr": &c.ExtensionMinimumLegATR, "minimum_leg_retracement": &c.MinimumLegRetracement,
		"expiry_hours": &c.ExpiryHours, "pip_size": &c.PipSize, "fibonacci_epsilon_atr": &c.FibonacciEpsilonATR,
		"confluence_star_three_ratio": &c.Confluence.StarThreeRatio, "confluence_star_two_ratio": &c.Confluence.StarTwoRatio,
		"confluence_zone_quality_weight": &c.Confluence.ZoneQualityWeight, "confluence_mad_score_weight": &c.Confluence.MADScoreWeight,
		"fibonacci_confluence_weight": &c.Confluence.FibonacciWeight,
	}
	for key, dst := range floats {
		value, err := strategyutil.Float(params, key)
		if err != nil {
			return Config{}, err
		}
		*dst = value
	}
	ints := map[string]*int{
		"confirmation_window_bars": &c.ConfirmationWindowBars, "confirmation_max_age_bars": &c.ConfirmationMaxAgeBars, "obstacle_pivot_bars": &c.ObstaclePivotBars,
		"leg_lookback_bars": &c.LegLookbackBars, "confluence_floor": &c.ConfluenceFloor,
	}
	for key, dst := range ints {
		value, err := strategyutil.Int(params, key)
		if err != nil {
			return Config{}, err
		}
		*dst = value
	}
	version, ok := params["confluence_scoring_version"].(string)
	if !ok || version == "" {
		return Config{}, fmt.Errorf("parameter %q must be a string", "confluence_scoring_version")
	}
	c.Confluence.ScoringVersion = version
	var err error
	if c.FibonacciEnabled, ok = params["fibonacci_enabled"].(bool); !ok {
		return Config{}, fmt.Errorf("parameter %q must be boolean", "fibonacci_enabled")
	}
	raw, present := params["structure_timeframes"]
	if !present {
		return Config{}, fmt.Errorf("missing required parameter %q", "structure_timeframes")
	}
	if c.StructureTimeframes, err = parseTimeframes(raw); err != nil {
		return Config{}, err
	}
	switch {
	case len(c.StructureTimeframes) == 0:
		return Config{}, fmt.Errorf("structure_timeframes must name at least one timeframe above M5")
	case c.MinimumGapATR < 0 || c.MinimumDisplacementBodyATR < 0 || c.MinimumDisplacementBodyRatio < 0 || c.CloseThroughBufferATR < 0:
		return Config{}, fmt.Errorf("the gap definition keys must be >= 0")
	case c.MaximumZoneATR <= 0 || c.EntryMaxWidthPrice <= 0 || c.MaximumEntryATR <= 0 || c.PipSize <= 0:
		return Config{}, fmt.Errorf("maximum_zone_atr, entry_max_width_price, maximum_entry_atr and pip_size must be > 0")
	case c.ConfirmationWindowBars < 1 || c.ConfirmationMaxAgeBars < 0 || c.MinimumWickRatio <= 0 || c.MinimumWickRatio > 1 || c.EngulfingMinimumRangeATR < 0:
		return Config{}, fmt.Errorf("invalid confirmation parameters")
	case c.InvalidationBufferATR <= 0 || c.MinimumTargetR <= 0 || c.FallbackTargetR < c.MinimumTargetR || c.ObstaclePivotBars < 1:
		return Config{}, fmt.Errorf("invalid stop or room parameters (fallback_target_r must be >= minimum_target_r)")
	case c.LegLookbackBars < 1 || c.ExtensionMinimumLegATR <= 0 || c.MinimumLegRetracement < 0 || c.MinimumLegRetracement > 1:
		return Config{}, fmt.Errorf("invalid extension parameters")
	case c.ConfluenceFloor < 1 || c.FibonacciEpsilonATR < 0 || c.ExpiryHours <= 0:
		return Config{}, fmt.Errorf("invalid qualification parameters")
	}
	if raw, present := params["bind_within_execution_window"]; present {
		if c.BindWithinExecutionWindow, ok = raw.(bool); !ok {
			return Config{}, fmt.Errorf("parameter %q must be boolean", "bind_within_execution_window")
		}
	}
	if c.StopBudget, err = strategyutil.ParseStopBudget(params); err != nil {
		return Config{}, err
	}
	return c, nil
}

// parseTimeframes reads a list of distinct recognised timeframes strictly above the execution timeframe (M5).
func parseTimeframes(raw any) ([]market.Timeframe, error) {
	items, ok := raw.([]any)
	if !ok {
		typed, isStrings := raw.([]string)
		if !isStrings {
			return nil, fmt.Errorf("parameter %q must be a list of timeframes", "structure_timeframes")
		}
		items = make([]any, len(typed))
		for i, item := range typed {
			items[i] = item
		}
	}
	seen := map[market.Timeframe]bool{}
	var out []market.Timeframe
	for _, item := range items {
		name, isString := item.(string)
		tf := market.Timeframe(name)
		minutes, known := tf.Minutes()
		if !isString || !known || minutes <= 5 || seen[tf] {
			return nil, fmt.Errorf("parameter %q must list distinct timeframes above M5, got %v", "structure_timeframes", item)
		}
		seen[tf] = true
		out = append(out, tf)
	}
	return out, nil
}
