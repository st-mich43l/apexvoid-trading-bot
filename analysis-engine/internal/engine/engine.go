// Package engine is runtime orchestration only — it wires
// internal/state.SymbolState together per symbol and produces
// AnalysisSnapshot, the one canonical, network-safe analysis result
// (source task §37). It must not contain indicator formulas or strategy
// formulas (§29) — those stay in their own packages; engine calls them.
//
// Not implemented this task beyond the two types below: per-symbol
// worker architecture (§30), the dependency-aware recomputation graph
// (§31), and the scheduler are proposed-tree entries
// (symbol_worker.go, event_router.go, dependency_graph.go, evaluator.go,
// scheduler.go) with no real computation to orchestrate yet — see
// docs/architecture/analysis-engine.md.
package engine

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/state"
)

// AnalysisSnapshot is the one canonical analysis result model (§37):
// what visualization, research, journal correlation, debugging, and
// replay all consume. Internal mutable SymbolState is never exposed
// directly as this network contract — Snapshot is always taken from it,
// never the other way around.
type AnalysisSnapshot struct {
	Symbol        market.Symbol
	Time          int64
	Context       context.MarketContext
	Opportunities []opportunity.Candidate
}

// Engine owns one SymbolState per symbol it tracks. Construction/update
// methods are not implemented this task (see package doc comment) — this
// type exists so the engine -> state dependency edge in
// docs/architecture/dependency-rules.md is real and checked by
// test/architecture/dependency_test.go, not only documented.
type Engine struct {
	symbols map[market.Symbol]*state.SymbolState
}

// NewEngine returns an Engine tracking no symbols yet.
func NewEngine() *Engine {
	return &Engine{symbols: make(map[market.Symbol]*state.SymbolState)}
}
