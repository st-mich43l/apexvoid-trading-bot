// Package visualization is observability, not decision logic (source task
// §35). It consumes internal/engine.AnalysisSnapshot and may render
// candles, swings, major/internal structure, BOS/CHoCH, liquidity, zones,
// strategy entry/invalidation/targets. It must never perform market
// analysis itself — a renderer that computes rather than draws is a
// boundary violation the same way a Telegram handler computing ATR would
// be (docs/architecture/dependency-rules.md).
//
// A leaf package: it may import any core analysis-engine type; nothing
// core imports it. Proposed files (chart.go, candle.go, structure.go,
// liquidity.go, zone.go, opportunity.go) not created this task — no
// renderer implementation exists yet to prove a boundary against.
package visualization
