package supply

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Config is the supply strategy's own parsed configuration (analysis.strategies.supply): every value the strategy reads and nothing it does not.
// Missing keys fail startup. The engine injects only the instrument scale (pip_size, entry_max_width_price, the stop cap) and the shared confluence rubric.
type Config struct {
	// StructureTimeframes are the frames a zone is found on. M5 only confirms and executes.
	StructureTimeframes []market.Timeframe

	// The zone: the range of the candles before a downward departure, a leg of at least DisplacementATRMult x ATR whose bodies are mostly at least
	// MomentumBodyFraction of their range.
	DisplacementATRMult  float64
	MomentumBodyFraction float64
	// MaximumZoneTouches is how many touches a zone may have taken and still be tradable (0: no limit).
	MaximumZoneTouches int
	// MaximumZoneATR is the widest zone, in ATR of its frame, that is a zone and not a region.
	MaximumZoneATR float64
	// EntryMaxWidthPrice clips a wide zone to its proximal slice (instrument scale, injected per instrument).
	EntryMaxWidthPrice float64
	MaximumEntryATR    float64

	// Confirmation (M5): the five reaction patterns within ReactionLookbackBars of a touch.
	ReactionLookbackBars     int
	EngulfingMinimumRangeATR float64

	// Stop and room.
	InvalidationBufferATR float64
	MinimumTargetR        float64
	FallbackTargetR       float64
	ObstaclePivotBars     int

	// Qualification.
	ConfluenceFloor     int
	FibonacciEnabled    bool
	FibonacciEpsilonATR float64
	Confluence          confluence.Config
	ExpiryHours         float64
	PipSize             float64

	// StopBudget is the instrument's stop envelope (strategyutil.StopBudget); the zero value is no budget.
	StopBudget strategyutil.StopBudget
}

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	floats := map[string]*float64{
		"displacement_atr_mult": &c.DisplacementATRMult, "momentum_body_fraction": &c.MomentumBodyFraction,
		"maximum_zone_atr": &c.MaximumZoneATR, "entry_max_width_price": &c.EntryMaxWidthPrice, "maximum_entry_atr": &c.MaximumEntryATR,
		"engulfing_minimum_range_atr": &c.EngulfingMinimumRangeATR, "invalidation_buffer_atr": &c.InvalidationBufferATR,
		"minimum_target_r": &c.MinimumTargetR, "fallback_target_r": &c.FallbackTargetR,
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
		"maximum_zone_touches": &c.MaximumZoneTouches, "reaction_lookback_bars": &c.ReactionLookbackBars, "obstacle_pivot_bars": &c.ObstaclePivotBars,
		"confluence_floor": &c.ConfluenceFloor,
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
	case c.DisplacementATRMult <= 0 || c.MomentumBodyFraction < 0 || c.MomentumBodyFraction > 1 || c.MaximumZoneTouches < 0:
		return Config{}, fmt.Errorf("invalid zone definition parameters")
	case c.MaximumZoneATR <= 0 || c.EntryMaxWidthPrice <= 0 || c.MaximumEntryATR <= 0 || c.PipSize <= 0:
		return Config{}, fmt.Errorf("maximum_zone_atr, entry_max_width_price, maximum_entry_atr and pip_size must be > 0")
	case c.ReactionLookbackBars < 1 || c.EngulfingMinimumRangeATR < 0:
		return Config{}, fmt.Errorf("invalid confirmation parameters")
	case c.InvalidationBufferATR <= 0 || c.MinimumTargetR <= 0 || c.FallbackTargetR < c.MinimumTargetR || c.ObstaclePivotBars < 1:
		return Config{}, fmt.Errorf("invalid stop or room parameters (fallback_target_r must be >= minimum_target_r)")
	case c.ConfluenceFloor < 1 || c.FibonacciEpsilonATR < 0 || c.ExpiryHours <= 0:
		return Config{}, fmt.Errorf("invalid qualification parameters")
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
