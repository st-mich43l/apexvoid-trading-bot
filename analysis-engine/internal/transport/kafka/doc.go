// Package kafka will hold the target Kafka consumer/producer/codec for
// the six event classes frozen in docs/adr/004-kafka-event-boundary.md
// (market.bar.closed.v1, market.tick.v1, analysis.opportunity.v1,
// analysis.opportunity.invalidated.v1, execution.trade-plan.v1,
// execution.trade-event.v1). Proposed files: consumer.go, producer.go,
// codec.go. Not implemented — no Kafka client dependency has been added
// to go.mod by this architecture task.
package kafka
