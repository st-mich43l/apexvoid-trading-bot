package strategyutil

import (
	"fmt"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Owner rule (23 Nov 2026): every strategy looks for its setup on M5, M15 and H1; M1 is execution only (it may time a fill, never find or confirm a setup). The helpers
// below only PARSE and ORDER: they decide nothing for a strategy, and nothing here filters a candidate on a strategy's behalf. Each strategy owns its own mechanism.

// StructureTimeframesKey is the parameter every strategy's own block uses for the frames its setups may be found on.
const StructureTimeframesKey = "structure_timeframes"

// RequireConfirmationKey is the optional per-strategy override of the global analysis.require_candle_confirmation.
const RequireConfirmationKey = "require_confirmation"

// GlobalConfirmationKey is the parameter the engine injects into every strategy's parameters with the global analysis.require_candle_confirmation value
// (engine.applyCandleConfirmation), the same way it injects the other shared leaves.
const GlobalConfirmationKey = "require_candle_confirmation"

// ParseStructureTimeframes reads the strategy's "structure_timeframes" list into an ordered, distinct list, in the order it was written. M1 is always refused
// ("M1 is execution only"); so is anything outside "allowed" (each strategy passes its own set) and anything that is not a known timeframe name, a duplicate, or an
// empty or missing list. Errors name the parameter and the offending entry.
func ParseStructureTimeframes(params map[string]any, allowed []market.Timeframe) ([]market.Timeframe, error) {
	raw, present := params[StructureTimeframesKey]
	if !present {
		return nil, fmt.Errorf("parameter %q is required", StructureTimeframesKey)
	}
	var items []any
	switch list := raw.(type) {
	case []any:
		items = list
	case []string:
		items = make([]any, len(list))
		for i, item := range list {
			items[i] = item
		}
	case []market.Timeframe:
		items = make([]any, len(list))
		for i, item := range list {
			items[i] = string(item)
		}
	default:
		return nil, fmt.Errorf("parameter %q must be a list of timeframes, got %T", StructureTimeframesKey, raw)
	}
	if len(items) == 0 {
		return nil, fmt.Errorf("parameter %q must list at least one timeframe", StructureTimeframesKey)
	}
	permitted := make(map[market.Timeframe]bool, len(allowed))
	names := make([]string, 0, len(allowed))
	for _, tf := range allowed {
		permitted[tf] = true
		names = append(names, string(tf))
	}
	seen := map[market.Timeframe]bool{}
	out := make([]market.Timeframe, 0, len(items))
	for _, item := range items {
		name, isString := item.(string)
		if !isString {
			return nil, fmt.Errorf("parameter %q entries must be timeframe names, got %v", StructureTimeframesKey, item)
		}
		tf := market.Timeframe(strings.ToUpper(strings.TrimSpace(name)))
		if _, known := tf.Minutes(); !known {
			return nil, fmt.Errorf("parameter %q: %q is not a timeframe", StructureTimeframesKey, name)
		}
		if tf == market.M1 {
			return nil, fmt.Errorf("parameter %q: M1 is execution only, it can never find or confirm a setup", StructureTimeframesKey)
		}
		if !permitted[tf] {
			return nil, fmt.Errorf("parameter %q: %s is not allowed for this strategy (allowed: %s)", StructureTimeframesKey, tf, strings.Join(names, ", "))
		}
		if seen[tf] {
			return nil, fmt.Errorf("parameter %q lists %s twice", StructureTimeframesKey, tf)
		}
		seen[tf] = true
		out = append(out, tf)
	}
	return out, nil
}

// ParseRequireConfirmation is whether a strategy needs a candle reaction before it publishes: its own optional "require_confirmation" when present, else
// "global" (analysis.require_candle_confirmation). A present value must be a boolean.
func ParseRequireConfirmation(params map[string]any, global bool) (bool, error) {
	raw, present := params[RequireConfirmationKey]
	if !present {
		return global, nil
	}
	value, ok := raw.(bool)
	if !ok {
		return false, fmt.Errorf("parameter %q must be a boolean, got %T", RequireConfirmationKey, raw)
	}
	return value, nil
}

// GlobalConfirmation is the global analysis.require_candle_confirmation as the engine injected it into the strategy's parameters (false when it was not injected).
func GlobalConfirmation(params map[string]any) bool {
	value, _ := params[GlobalConfirmationKey].(bool)
	return value
}

// ThesisRank is what the duplicate-thesis order compares: the frame a structure was found on, its confluence stars and the distance from the price to the entry.
type ThesisRank struct {
	Timeframe     market.Timeframe
	Stars         int
	EntryDistance float64
}

// PreferHigherTimeframe is the order of the duplicate-thesis rule: when the same structure qualifies on more than one timeframe a strategy publishes one candidate
// per thesis, the higher timeframe first, then more confluence stars, then the nearer entry. It reports whether "a" is preferred over "b" (false on a full tie,
// so a stable sort keeps the earlier one). It never filters: the strategy decides which candidates are one thesis and applies the order itself.
func PreferHigherTimeframe(a, b ThesisRank) bool {
	am, _ := a.Timeframe.Minutes()
	bm, _ := b.Timeframe.Minutes()
	if am != bm {
		return am > bm
	}
	if a.Stars != b.Stars {
		return a.Stars > b.Stars
	}
	return a.EntryDistance < b.EntryDistance
}
