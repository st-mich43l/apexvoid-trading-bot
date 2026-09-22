package marketdata

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// BarEvent is one normalized closed-bar event — what a market data feed
// hands to MarketHistory.Append (and what cmd/replay feeds through the
// exact same path chronologically, per docs/analysis/market-structure-v2.md's
// replay-causality section: no special replay-only ingestion code).
type BarEvent struct {
	Symbol    market.Symbol
	Timeframe market.Timeframe
	Candle    market.Candle
}
