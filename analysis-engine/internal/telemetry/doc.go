// Package telemetry measures the engine, per source task §36: event
// processing duration, state update duration, indicator/structure/zone/
// strategy duration, queue age, events processed/rejected,
// duplicate/out-of-order events. No unbounded metric labels (§36) — a
// future implementation must not key a metric by, e.g., a raw candidate
// ID or timestamp.
//
// A leaf package like internal/visualization: may import any core type;
// nothing core imports it. Proposed files (metrics.go, timing.go,
// counters.go, diagnostics.go) not created this task — no metrics
// implementation exists yet to prove a boundary against.
package telemetry
