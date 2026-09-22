package marketdata

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Bootstrap bulk-loads events into h through the exact same Append path a
// live feed uses — one call per event, in the order given. This is
// deliberate, not an optimization shortcut: source task §47 requires
// replay to use "the exact live engine path... no special replay-only
// structure implementation," and startup bootstrap is the same
// requirement one level down — a batch of historical bars loaded at
// startup must produce byte-identical TimeframeHistory state to those
// same bars having arrived one at a time live. Events must already be
// time-ordered per timeframe; Append's own sequencing policy (see
// timeframe_history.go) is what actually enforces that, not this
// function — an out-of-order event in the input is reported via its
// AppendResult, not silently reordered.
func Bootstrap(h *MarketHistory, events []BarEvent) []AppendResult {
	results := make([]AppendResult, len(events))
	for i, event := range events {
		result, _ := h.Append(event)
		results[i] = result
	}
	return results
}

// BootstrapTimeframe is Bootstrap narrowed to one already-known-correct
// TimeframeHistory + Symbol/Timeframe pairing — useful for tests and for
// callers (e.g. a single-timeframe replay tool) that don't need the full
// MarketHistory symbol/timeframe routing.
func BootstrapTimeframe(h *TimeframeHistory, candles []market.Candle) []AppendResult {
	results := make([]AppendResult, len(candles))
	for i, c := range candles {
		result, _ := h.Append(c)
		results[i] = result
	}
	return results
}
