// Package transport integrates external transport only — it must not know
// trading strategy rules (source task §32). Two subpackages:
// transport/kafka (the target durable inter-service event bus, ADR-004 —
// not implemented; no broker or client library exists in this repo yet)
// and transport/redis (today's real cross-service transport and, once
// Kafka cutover happens, the narrower cache/state role — ADR-004,
// event-flow.md's Redis section).
//
// No client code lives here yet. This package exists so
// internal/engine's future publish path has a real import target to
// prove the engine -> transport dependency edge against, once there is
// something to publish.
package transport
