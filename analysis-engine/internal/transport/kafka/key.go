package kafka

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// RecordKey is the Kafka partition key for a neutral opportunity event:
// the canonical symbol alone. It keeps created and invalidated lifecycle
// events for one symbol ordered without teaching Kafka about strategies.
func RecordKey(symbol market.Symbol) []byte {
	return []byte(symbol)
}
