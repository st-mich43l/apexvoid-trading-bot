// Package marketdata owns market-event normalization and rolling history:
// receiving raw bar/tick events, normalizing symbol/timeframe identity,
// validating sequencing, deduplicating, maintaining rolling history, and
// bootstrapping historical bars on startup. See
// docs/architecture/analysis-engine.md's "Market history sizing" section
// for the per-timeframe minimums this package's history/bootstrap logic
// must support (M1 1,000-2,000 candles ... H4 200-300).
//
// It must not interpret what the data means (no swing/structure/zone
// logic — that is internal/structure, internal/liquidity, internal/zone)
// and must not decide anything (no strategy logic).
//
// internal/market's CandleWindow (a bounded ring buffer) already covers
// part of this package's eventual "rolling history" responsibility for a
// single symbol+timeframe; whether CandleWindow moves here in full, or
// marketdata wraps it with the multi-timeframe bootstrap/normalization
// layer on top, is a Stage 1-continuation implementation decision, not
// resolved by this architecture task — see docs/architecture/migration-map.md.
//
// No production code lives here yet; this file exists to reserve the
// package's boundary in the dependency graph
// (docs/architecture/dependency-rules.md), not to implement anything.
package marketdata
