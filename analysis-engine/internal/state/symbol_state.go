// Package state holds the engine's canonical state: SymbolState, the one
// analytical truth for one symbol (docs/adr/005-canonical-symbol-state.md).
// Strategies read it; nothing recomputes what it already holds (source
// task §28).
package state

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// SymbolState is one symbol's canonical analytical state, per source task
// §40.
type SymbolState struct {
	Symbol market.Symbol

	History      *marketdata.MarketHistory
	Measurements *MeasurementBook

	Structure     *structure.Book
	Zone          *zone.Book
	Liquidity     *liquidity.Book
	Trendline     *trendline.Book
	KeyLevel      *keylevel.Book
	Session       *session.Book
	Fib           *fib.Book
	Context       context.MarketContext
	Opportunities *opportunity.Book
}

// NewSymbolState returns an empty SymbolState with History bounded per
// depths (config/analysis.yml's analysis.history.depth.*, read by
// internal/engine — state itself has no config dependency) and every
// other book initialized empty, ready for internal/engine to populate.
func NewSymbolState(symbol market.Symbol, depths map[market.Timeframe]int, allowReplaceForming bool) (*SymbolState, error) {
	history, err := marketdata.NewMarketHistory(symbol, depths, allowReplaceForming)
	if err != nil {
		return nil, err
	}
	return &SymbolState{
		Symbol:        symbol,
		History:       history,
		Measurements:  NewMeasurementBook(),
		Structure:     structure.NewBook(),
		Zone:          zone.NewBook(),
		Liquidity:     liquidity.NewBook(),
		Trendline:     trendline.NewBook(),
		KeyLevel:      keylevel.NewBook(),
		Session:       session.NewBook(),
		Fib:           fib.NewBook(),
		Opportunities: opportunity.NewBook(),
	}, nil
}

// MeasurementBook is Canonical Measurements — the pipeline stage between
// MarketHistory and Structure V2 (source task §1's own diagram: "Market
// History -> Canonical Measurements -> Market Structure V2"). Today this
// is the canonical ATR series per timeframe (indicator.CanonicalATR's
// output) — the one series structure.Update/liquidity.Update must be
// given, never a second independently-computed one (source task §27).
type MeasurementBook struct {
	ATRByTimeframe map[market.Timeframe][]float64
}

// NewMeasurementBook returns an empty MeasurementBook.
func NewMeasurementBook() *MeasurementBook {
	return &MeasurementBook{ATRByTimeframe: make(map[market.Timeframe][]float64)}
}

// SetATR stores tf's canonical ATR series, index-aligned with whatever
// candle slice produced it (the caller's responsibility to keep paired —
// see internal/engine, which always computes and consumes them together).
func (m *MeasurementBook) SetATR(tf market.Timeframe, series []float64) {
	m.ATRByTimeframe[tf] = series
}

// ATR returns tf's canonical ATR series, or nil if none has been computed
// yet.
func (m *MeasurementBook) ATR(tf market.Timeframe) []float64 {
	return m.ATRByTimeframe[tf]
}
