package marketdata

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// MarketHistory holds one bounded TimeframeHistory per timeframe for one
// symbol — source task §7. Deliberately has NO dependency on
// internal/config: turning "analysis.history.depth.*" into the
// map[Timeframe]int this constructor takes is internal/engine's job (the
// composition layer), not marketdata's — marketdata and config are
// same-rank siblings in docs/architecture/dependency-rules.md's graph, and
// neither may import the other.
type MarketHistory struct {
	Symbol     market.Symbol
	Timeframes map[market.Timeframe]*TimeframeHistory
}

// NewMarketHistory builds one TimeframeHistory per entry in depths, all
// sharing the same allowReplace policy. depths with a non-positive value
// or an unrecognized Timeframe are rejected up front (fail closed, §12 of
// the architecture task) rather than deferred to the first Append against
// that timeframe.
func NewMarketHistory(symbol market.Symbol, depths map[market.Timeframe]int, allowReplace bool) (*MarketHistory, error) {
	timeframes := make(map[market.Timeframe]*TimeframeHistory, len(depths))
	for tf, depth := range depths {
		if err := ValidateTimeframe(tf); err != nil {
			return nil, fmt.Errorf("marketdata: history depth for %w", err)
		}
		if depth <= 0 {
			return nil, fmt.Errorf("marketdata: history depth for %s must be positive, got %d", tf, depth)
		}
		timeframes[tf] = NewTimeframeHistory(symbol, tf, depth, allowReplace)
	}
	return &MarketHistory{Symbol: symbol, Timeframes: timeframes}, nil
}

// Append routes event to its timeframe's history. Rejects (as
// AppendRejectedInvalid) an event for a symbol other than h.Symbol, or a
// timeframe this MarketHistory was not configured to hold — both fail
// closed rather than silently create a new series on the fly, matching
// §58's "unknown symbol/timeframe must fail closed."
func (h *MarketHistory) Append(event BarEvent) (AppendResult, error) {
	if event.Symbol != h.Symbol {
		return AppendRejectedInvalid, fmt.Errorf(
			"marketdata: event symbol %s does not match history symbol %s", event.Symbol, h.Symbol,
		)
	}
	tfh, ok := h.Timeframes[event.Timeframe]
	if !ok {
		return AppendRejectedInvalid, fmt.Errorf(
			"marketdata: timeframe %s is not configured for symbol %s", event.Timeframe, h.Symbol,
		)
	}
	return tfh.Append(event.Candle)
}

// For returns this symbol's history for one timeframe, or nil if that
// timeframe was not configured.
func (h *MarketHistory) For(tf market.Timeframe) *TimeframeHistory {
	return h.Timeframes[tf]
}
