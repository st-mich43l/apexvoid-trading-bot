package structure

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Book is the structure-domain slice of a symbol's canonical state —
// internal/state.SymbolState holds one of these per symbol, one Book
// entry per tracked timeframe (a symbol's M5 structure and M15 structure
// are distinct StructureStates, not merged into one).
type Book struct {
	ByTimeframe map[market.Timeframe]StructureState
}

// NewBook returns an empty Book ready for Set/Get.
func NewBook() *Book {
	return &Book{ByTimeframe: make(map[market.Timeframe]StructureState)}
}

// Set stores tf's latest StructureState, replacing whatever was there.
func (b *Book) Set(tf market.Timeframe, state StructureState) {
	b.ByTimeframe[tf] = state
}

// Get returns tf's latest StructureState, or the zero value and false if
// none has been computed yet.
func (b *Book) Get(tf market.Timeframe) (StructureState, bool) {
	state, ok := b.ByTimeframe[tf]
	return state, ok
}
