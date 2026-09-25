package liquidity

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Book is the liquidity-domain slice of a symbol's canonical state —
// internal/state.SymbolState holds one of these per symbol, one entry per
// tracked timeframe, mirroring structure.Book.
type Book struct {
	ByTimeframe map[market.Timeframe]LiquidityState
}

// NewBook returns an empty Book ready for Set/Get.
func NewBook() *Book {
	return &Book{ByTimeframe: make(map[market.Timeframe]LiquidityState)}
}

// Set stores tf's latest LiquidityState, replacing whatever was there.
func (b *Book) Set(tf market.Timeframe, state LiquidityState) {
	b.ByTimeframe[tf] = state
}

// Get returns tf's latest LiquidityState, or the zero value and false if
// none has been computed yet.
func (b *Book) Get(tf market.Timeframe) (LiquidityState, bool) {
	state, ok := b.ByTimeframe[tf]
	return state, ok
}
