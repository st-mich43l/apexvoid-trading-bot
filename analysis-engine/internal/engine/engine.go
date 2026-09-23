// Package engine is runtime orchestration only — it wires
// internal/state.SymbolState together per symbol (via SymbolWorker) and
// produces AnalysisSnapshot (snapshot.go). It must not contain indicator
// formulas or strategy formulas (source task §29) — those stay in their
// own packages; engine calls them. config.go is the one place
// internal/config becomes reachable by the domain packages this file
// wires together.
package engine

import (
	"fmt"
	"sync"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
)

// Engine owns one SymbolWorker per tracked symbol — source task §30:
// "market events -> event router -> XAU/EURUSD/GBPJPY workers." Dispatch
// routes an event to its symbol's own worker; different symbols'
// SymbolWorkers hold independent locks, so concurrent Dispatch calls for
// different symbols never block each other, while same-symbol calls
// always serialize through that symbol's own worker (§41).
type Engine struct {
	mu        sync.RWMutex
	workers   map[market.Symbol]*SymbolWorker
	settings  map[market.Symbol]Settings
	telemetry *telemetry.Recorder
	publisher *OpportunityPublisher // nil = no Kafka opportunity publication (Phase S9)
}

// NewEngine returns an Engine tracking no symbols yet. recorder may be
// nil (a fresh telemetry.Recorder is created); pass a shared one when the
// caller wants one Recorder's Snapshot to cover every tracked symbol.
func NewEngine(recorder *telemetry.Recorder) *Engine {
	if recorder == nil {
		recorder = telemetry.NewRecorder()
	}
	return &Engine{
		workers: make(map[market.Symbol]*SymbolWorker), settings: make(map[market.Symbol]Settings),
		telemetry: recorder,
	}
}

// Register adds symbol with its own Settings (a symbol's history depths/
// structure/liquidity config may differ once per-instrument overrides
// exist — config/instruments.yml already has this shape for other
// domains, per-symbol Analysis Engine V2 overrides are not implemented
// this task, see docs/analysis-engine-v2-migration.md). Registering an
// already-tracked symbol replaces its worker (and therefore its state) —
// callers that want to preserve state across a settings change must not
// call Register a second time.
func (e *Engine) Register(symbol market.Symbol, settings Settings) error {
	e.mu.RLock()
	publisher := e.publisher
	e.mu.RUnlock()
	worker, err := NewSymbolWorker(symbol, settings, e.telemetry, publisher)
	if err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	e.workers[symbol] = worker
	e.settings[symbol] = settings
	return nil
}

// SetPublisher wires opportunity Kafka publication (Phase S9) — call
// before Register-ing any symbol that should publish, since Register
// reads the current publisher once at worker-construction time, not on
// every event. pub may be nil (the default) to disable publication
// entirely, matching OpportunityPublisher's own nil-safe Enqueue/Run.
func (e *Engine) SetPublisher(pub *OpportunityPublisher) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.publisher = pub
}

// Dispatch routes event to its symbol's worker. Returns an error if the
// symbol was never Register-ed — fail closed (source task §58), never
// silently create a worker with guessed settings on the fly.
func (e *Engine) Dispatch(event marketdata.BarEvent) (AnalysisSnapshot, error) {
	snapshot, _, err := e.DispatchWithResult(event)
	return snapshot, err
}

// DispatchWithResult routes one closed bar and returns the market-history
// sequencing outcome as well as the snapshot. Transport runtimes use this
// to expose duplicate/conflict/out-of-order telemetry without inventing a
// second Redis-specific deduplication policy.
func (e *Engine) DispatchWithResult(event marketdata.BarEvent) (AnalysisSnapshot, marketdata.AppendResult, error) {
	e.mu.RLock()
	worker, ok := e.workers[event.Symbol]
	e.mu.RUnlock()
	if !ok {
		return AnalysisSnapshot{}, marketdata.AppendRejectedInvalid, fmt.Errorf("engine: symbol %s is not registered", event.Symbol)
	}
	return worker.ApplyWithResult(event)
}

// Snapshot returns symbol's current AnalysisSnapshot without applying a
// new event.
func (e *Engine) Snapshot(symbol market.Symbol, now int64) (AnalysisSnapshot, error) {
	e.mu.RLock()
	worker, ok := e.workers[symbol]
	e.mu.RUnlock()
	if !ok {
		return AnalysisSnapshot{}, fmt.Errorf("engine: symbol %s is not registered", symbol)
	}
	return worker.Snapshot(now), nil
}

// Telemetry exposes the shared Recorder so a caller (cmd/analysis-engine,
// a future metrics endpoint, or a test) can read it — see
// telemetry.Recorder.Snapshot.
func (e *Engine) Telemetry() *telemetry.Recorder { return e.telemetry }
