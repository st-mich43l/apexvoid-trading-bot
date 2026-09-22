package kafka

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// RecordKey is the Kafka partition key for a market-domain event: the
// canonical symbol alone, unconditionally (source task §14/§15) — never
// timeframe-qualified, never a random event ID, never the broker symbol.
// Every timeframe of one symbol's events therefore share a partition and
// Kafka's own within-partition ordering guarantee, matching the frozen
// per-symbol-FIFO worker architecture
// (docs/architecture/dependency-rules.md, internal/engine.SymbolWorker):
// XAU M1/M5/M15/H1 all key to "XAU"; EURUSD keys to a different value
// and may land on a different partition, processing concurrently.
//
// This is NOT the same thing as BarIdentity (record.go) — that is the
// deduplication identity (symbol + timeframe + close time); this key
// only controls partition placement/ordering.
func RecordKey(symbol market.Symbol) []byte {
	return []byte(symbol)
}
