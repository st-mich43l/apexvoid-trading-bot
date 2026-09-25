// Package kafka implements analysis-engine's real Kafka transport
// (ADR-008, ADR-009, ADR-010, docs/transport/kafka.md). It owns broker
// connection, producer, serialization, event metadata (the shared Envelope),
// opportunity record-key selection, shutdown, health, and transport telemetry.
// Analysis Engine is deliberately producer-only because Redis owns the
// market-data plane.
//
// It does NOT own: market structure, strategy evaluation, business
// risk, TradePlan construction, broker execution, or Telegram (source
// task §2) — Kafka is transport, never technical-analysis logic (§1).
//
// Dependency rank 7 (docs/architecture/dependency-rules.md's third
// amendment): strictly above opportunity(5)/strategy/confluence/state(6),
// strictly below engine(8) — engine is the only package permitted to
// import this one. structure/liquidity/zone/indicator/strategy must
// never import it (enforced by
// analysis-engine/test/architecture/dependency_test.go).
//
// Client library: github.com/twmb/franz-go, pinned at v1.19.5 — see
// ADR-008 for why this client and this exact version.
package kafka
