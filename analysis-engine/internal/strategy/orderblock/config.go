package orderblock

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Config is the order block strategy's own parsed configuration: every value the strategy reads, nothing it does not.
type Config struct {
	// BreakBufferATR retires a block once a close of its own frame is beyond its far edge by more than this share of that bar's ATR (0 switches the rule off).
	BreakBufferATR float64
	// MaximumZoneTouches is how many touches a block may have taken and still be tradable (0: no limit).
	MaximumZoneTouches int
	// Geometry.
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
	PipSize                  float64
	EntryMaxWidthPrice       float64
	MaximumEntryATR          float64
	// Qualification.
	ConfluenceFloor          int
	Confluence               confluence.Config
	FibonacciEnabled         bool
	FibonacciEpsilonATR      float64
	ReactionLookbackBars     int
	EngulfingMinimumRangeATR float64
	// HigherTimeframes are the frames whose order blocks this strategy also trades (found there, confirmed and executed on M5).
	HigherTimeframes []market.Timeframe
	// StopBudget is the instrument's stop envelope (strategyutil.StopBudget); the zero value is no budget.
	StopBudget strategyutil.StopBudget
}

func parseConfig(params map[string]any) (Config, error) {
	var c Config
	floats := map[string]*float64{
		"invalidation_buffer_atr": &c.InvalidationBufferATR, "minimum_target_distance_atr": &c.MinimumTargetDistanceATR, "expiry_hours": &c.ExpiryHours,
		"pip_size": &c.PipSize, "fvg_entry_max_width_price": &c.EntryMaxWidthPrice, "maximum_entry_atr": &c.MaximumEntryATR,
		"confluence_star_three_ratio": &c.Confluence.StarThreeRatio, "confluence_star_two_ratio": &c.Confluence.StarTwoRatio,
		"confluence_zone_quality_weight": &c.Confluence.ZoneQualityWeight, "confluence_mad_score_weight": &c.Confluence.MADScoreWeight,
		"fibonacci_confluence_weight": &c.Confluence.FibonacciWeight, "fibonacci_epsilon_atr": &c.FibonacciEpsilonATR,
		"engulfing_minimum_range_atr": &c.EngulfingMinimumRangeATR,
	}
	for key, dst := range floats {
		value, err := strategyutil.Float(params, key)
		if err != nil {
			return Config{}, err
		}
		*dst = value
	}
	var err error
	if c.ConfluenceFloor, err = strategyutil.Int(params, "confluence_floor"); err != nil {
		return Config{}, err
	}
	if c.ReactionLookbackBars, err = strategyutil.Int(params, "reaction_lookback_bars"); err != nil {
		return Config{}, err
	}
	if _, present := params["confirmed_break_buffer_atr"]; present {
		if c.BreakBufferATR, err = strategyutil.Float(params, "confirmed_break_buffer_atr"); err != nil {
			return Config{}, err
		}
	}
	if _, present := params["maximum_zone_touches"]; present {
		if c.MaximumZoneTouches, err = strategyutil.Int(params, "maximum_zone_touches"); err != nil {
			return Config{}, err
		}
	}
	version, ok := params["confluence_scoring_version"].(string)
	if !ok || version == "" {
		return Config{}, fmt.Errorf("parameter %q must be a string", "confluence_scoring_version")
	}
	c.Confluence.ScoringVersion = version
	if c.FibonacciEnabled, ok = params["fibonacci_enabled"].(bool); !ok {
		return Config{}, fmt.Errorf("parameter %q must be boolean", "fibonacci_enabled")
	}
	if raw, present := params["higher_timeframes"]; present {
		if c.HigherTimeframes, err = parseTimeframes(raw); err != nil {
			return Config{}, err
		}
	}
	switch {
	case c.BreakBufferATR < 0 || c.MaximumZoneTouches < 0:
		return Config{}, fmt.Errorf("confirmed_break_buffer_atr and maximum_zone_touches must be >= 0")
	case c.InvalidationBufferATR <= 0:
		return Config{}, fmt.Errorf("invalidation_buffer_atr must be > 0")
	case c.ExpiryHours <= 0:
		return Config{}, fmt.Errorf("expiry_hours must be > 0")
	case c.PipSize <= 0 || c.MaximumEntryATR <= 0 || c.EntryMaxWidthPrice < 0:
		return Config{}, fmt.Errorf("pip_size and maximum_entry_atr must be > 0 and fvg_entry_max_width_price >= 0")
	case c.ConfluenceFloor < 1 || c.ReactionLookbackBars < 1 || c.EngulfingMinimumRangeATR < 0 || c.FibonacciEpsilonATR < 0:
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
			return nil, fmt.Errorf("parameter %q must be a list of timeframes", "higher_timeframes")
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
			return nil, fmt.Errorf("parameter %q must list distinct timeframes above M5, got %v", "higher_timeframes", item)
		}
		seen[tf] = true
		out = append(out, tf)
	}
	return out, nil
}
