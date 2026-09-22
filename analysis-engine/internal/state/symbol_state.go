// Package state holds the engine's canonical state: SymbolState, the one
// analytical truth for one symbol (docs/adr/005-canonical-symbol-state.md).
// Strategies read it; nothing recomputes what it already holds (source
// task §28).
package state

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// SymbolState is one symbol's canonical analytical state, per the source
// task's §27. MarketHistory (per-timeframe CandleWindow) is deliberately
// not embedded here yet — it belongs to internal/marketdata, which has no
// implementation to hold a reference to yet (see marketdata/doc.go);
// adding a placeholder field for it now would prove nothing the other
// fields don't already prove about the state -> {structure, liquidity,
// zone, context, opportunity} dependency edges.
type SymbolState struct {
	Symbol market.Symbol

	Structure     *structure.Book
	Liquidity     *liquidity.Book
	Zones         *zone.Book
	Context       context.MarketContext
	Opportunities *opportunity.Book
}
