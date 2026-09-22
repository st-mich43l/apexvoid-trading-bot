package kafka

import (
	"context"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
)

// Handler is the small interface transport hands a decoded, validated,
// typed event to (source task §24: "do not expose raw Kafka client
// objects throughout the engine. Only composition/bootstrap should know
// Kafka implementation details."). internal/engine (rank 8, the only
// package permitted to import this one at rank 7) implements this for
// real by routing to Engine.Dispatch; test doubles implement it for
// consumer_test.go without needing a real engine.
//
// A typed-equivalent of the source task's own illustrative
// `Handle(ctx, Event) error` — two methods instead of one generic Event
// sum type, because market.bar.closed.v1 already has a real internal
// domain type to adapt into (marketdata.BarEvent) while market.tick.v1
// does not yet (no tick pipeline exists elsewhere in this codebase), so
// forcing them through one shared Event type would either fabricate a
// domain type for ticks that nothing else uses, or leave BarClosed
// under-typed to match tick's own lesser shape. Every returned error
// should be classified via Permanent/Transient (errors.go); an
// unclassified error defaults to transient (IsPermanent's doc comment).
type Handler interface {
	HandleBarClosed(ctx context.Context, event marketdata.BarEvent) error

	// HandleTick has no real internal domain type to hand back yet.
	// TickPayload is passed through as decoded until one exists.
	// Disabled by default (Config.TickConsumptionEnabled=false, source
	// task §3), so this rarely runs in practice.
	HandleTick(ctx context.Context, tick TickPayload) error
}
