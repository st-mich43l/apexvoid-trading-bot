package marketdata

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// EventOrigin describes why an otherwise identical closed bar reached the
// engine. It is transport provenance, not an analytical input: structure and
// strategies must produce the same market state for every origin.
type EventOrigin string

const (
	// EventOriginLive is a newly observed or recovered closed bar. The zero
	// value intentionally means live for backward-compatible direct callers.
	EventOriginLive EventOrigin = "live"
	// EventOriginBootstrap reconstructs retained state at process startup.
	// It may create lifecycle records but must never republish historical
	// opportunities as new real-time Kafka events.
	EventOriginBootstrap EventOrigin = "bootstrap"
	// EventOriginReplay is offline research input and is never publishable.
	EventOriginReplay EventOrigin = "replay"
)

// BarEvent is one normalized closed-bar event — what a market data feed
// hands to MarketHistory.Append (and what cmd/replay feeds through the
// exact same path chronologically, per docs/analysis/market-structure-v2.md's
// replay-causality section: no special replay-only ingestion code).
type BarEvent struct {
	Symbol    market.Symbol
	Timeframe market.Timeframe
	Candle    market.Candle
	Origin    EventOrigin
}

// PublishesOpportunity reports whether a lifecycle transition caused by this
// event may enter the Kafka business-event plane. Bootstrap and replay build
// the exact same technical state but are not new market observations.
func (e BarEvent) PublishesOpportunity() bool {
	return e.Origin == "" || e.Origin == EventOriginLive
}
