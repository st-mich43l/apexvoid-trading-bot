package strategyutil

import (
	"fmt"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// ParseNumbers reads every required numeric parameter of a strategy into its field. A missing or non-numeric key fails with the strategy's name, so a
// strategy never trades a guessed value.
func ParseNumbers(strategy string, params map[string]any, floats map[string]*float64, ints map[string]*int) error {
	keys := make([]string, 0, len(floats))
	for k := range floats {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, key := range keys {
		v, err := Float(params, key)
		if err != nil {
			return fmt.Errorf("%s: %w", strategy, err)
		}
		*floats[key] = v
	}
	keys = keys[:0]
	for k := range ints {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, key := range keys {
		v, err := Int(params, key)
		if err != nil {
			return fmt.Errorf("%s: %w", strategy, err)
		}
		*ints[key] = v
	}
	return nil
}

// ParseSymbols reads the optional "symbols" list (an instrument scope); absent means every instrument.
func ParseSymbols(params map[string]any) ([]string, error) {
	raw, present := params["symbols"]
	if !present {
		return nil, nil
	}
	list, ok := raw.([]any)
	if !ok {
		return nil, fmt.Errorf("symbols must be a list")
	}
	var out []string
	for _, item := range list {
		name, ok := item.(string)
		if !ok {
			return nil, fmt.Errorf("symbols entries must be strings")
		}
		out = append(out, name)
	}
	return out, nil
}

// SymbolAllowed reports whether the instrument is inside the strategy's scope (an empty scope allows every instrument).
func SymbolAllowed(scope []string, symbol market.Symbol) bool {
	if len(scope) == 0 {
		return true
	}
	for _, allowed := range scope {
		if allowed == string(symbol) {
			return true
		}
	}
	return false
}

// TradingDay is the trading-day number of a Unix time for a calendar that rolls over at rolloverUTCHour (the session calendar's own rollover).
func TradingDay(unix int64, rolloverUTCHour int) int64 {
	return (unix - int64(rolloverUTCHour)*3600) / 86400
}
