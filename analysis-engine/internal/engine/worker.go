package engine

import (
	"sync"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/state"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// SymbolWorker owns one symbol's mutable SymbolState and every update to
// it — source task §30/§41: "one symbol worker owns that symbol's
// mutable state... FIFO per symbol." Apply's own mutex is what provides
// that FIFO guarantee: two goroutines calling Apply for the SAME symbol
// serialize; different symbols' SymbolWorkers never share a lock, so they
// process concurrently (proven under `go test -race ./...`, not just
// documented — see test/engine/worker_test.go).
type SymbolWorker struct {
	mu        sync.Mutex
	state     *state.SymbolState
	settings  Settings
	telemetry *telemetry.Recorder
}

// NewSymbolWorker returns a worker for symbol with an empty SymbolState
// bounded per settings.HistoryDepths.
func NewSymbolWorker(symbol market.Symbol, settings Settings, recorder *telemetry.Recorder) (*SymbolWorker, error) {
	ws, err := state.NewSymbolState(symbol, settings.HistoryDepths, settings.AllowReplaceForming)
	if err != nil {
		return nil, err
	}
	if recorder == nil {
		recorder = telemetry.NewRecorder()
	}
	return &SymbolWorker{state: ws, settings: settings, telemetry: recorder}, nil
}

// Apply processes one closed-bar event through the full pipeline —
// source task §42's dependency-aware update: ONLY the timeframe that just
// closed gets a new structure.Update/liquidity.Update pass (each
// timeframe's structure is self-contained, computed from its own candle
// window — an M1 close never triggers H1 structure recomputation, because
// H1's own Update is never called by an M1 event in the first place).
// MarketContext is rebuilt after every accepted event, combining whatever
// timeframes have been computed so far — cheap (map assembly, no
// re-analysis), so it does not need its own dirty-tracking.
//
// A duplicate/out-of-order/invalid event still returns the current
// snapshot (unchanged) rather than an error — source task §8's explicit-
// outcome contract is satisfied by the AppendResult itself, already
// surfaced via telemetry counters; Apply's return value staying a valid
// snapshot either way keeps every caller's control flow uniform.
func (w *SymbolWorker) Apply(event marketdata.BarEvent) (AnalysisSnapshot, error) {
	snapshot, _, err := w.ApplyWithResult(event)
	return snapshot, err
}

// ApplyWithResult is Apply with the MarketHistory append disposition made
// explicit for a transport runtime. Domain sequencing remains owned by
// marketdata; callers only observe the result for telemetry and cursor
// decisions.
func (w *SymbolWorker) ApplyWithResult(event marketdata.BarEvent) (AnalysisSnapshot, marketdata.AppendResult, error) {
	w.mu.Lock()
	defer w.mu.Unlock()

	symbolLabel := string(w.state.Symbol)
	tfLabel := string(event.Timeframe)
	done := w.telemetry.Time(telemetry.PhaseEventTotal, symbolLabel, tfLabel)
	defer done()

	doneMD := w.telemetry.Time(telemetry.PhaseMarketData, symbolLabel, tfLabel)
	result, err := w.state.History.Append(event)
	doneMD()

	switch {
	case err != nil:
		w.telemetry.Count(telemetry.CounterEventsRejected, symbolLabel, tfLabel, 1)
		return AnalysisSnapshot{}, result, err
	case result == marketdata.AppendDuplicate:
		w.telemetry.Count(telemetry.CounterDuplicateEvents, symbolLabel, tfLabel, 1)
		return SnapshotFrom(w.state, w.settings, event.Candle.Time), result, nil
	case result == marketdata.AppendOutOfOrder:
		w.telemetry.Count(telemetry.CounterOutOfOrderEvents, symbolLabel, tfLabel, 1)
		return SnapshotFrom(w.state, w.settings, event.Candle.Time), result, nil
	case result == marketdata.AppendConflict:
		// Correction policy (documented by ADR-010): the
		// original, already-analyzed candle is kept — structure/liquidity
		// already computed from it must never be silently retroactively
		// rewritten — and the conflict is counted under its own distinct
		// counter so an operator can see it happened, unlike an ordinary
		// duplicate which is expected and benign.
		w.telemetry.Count(telemetry.CounterConflictEvents, symbolLabel, tfLabel, 1)
		return SnapshotFrom(w.state, w.settings, event.Candle.Time), result, nil
	}
	w.telemetry.Count(telemetry.CounterEventsProcessed, symbolLabel, tfLabel, 1)

	tfh := w.state.History.For(event.Timeframe)
	candles := tfh.Snapshot()

	doneInd := w.telemetry.Time(telemetry.PhaseIndicator, symbolLabel, tfLabel)
	atrSeries, err := indicator.CanonicalATR(candles, w.settings.ATR.Length, w.settings.ATR.Algorithm)
	doneInd()
	if err != nil {
		return AnalysisSnapshot{}, result, err
	}
	w.state.Measurements.SetATR(event.Timeframe, atrSeries)

	doneStruct := w.telemetry.Time(telemetry.PhaseStructure, symbolLabel, tfLabel)
	structState := structure.Update(candles, atrSeries, event.Timeframe, w.settings.Structure)
	doneStruct()
	w.state.Structure.Set(event.Timeframe, structState)

	doneZone := w.telemetry.Time(telemetry.PhaseZone, symbolLabel, tfLabel)
	zoneState := zone.Update(candles, atrSeries, structState.Swings, structState.Breaks, event.Timeframe, w.settings.Zone)
	doneZone()
	w.state.Zone.Set(event.Timeframe, zoneState)

	doneLiq := w.telemetry.Time(telemetry.PhaseLiquidity, symbolLabel, tfLabel)
	liqState := liquidity.Update(candles, atrSeries, structState.Swings, w.settings.Liquidity)
	doneLiq()
	w.state.Liquidity.Set(event.Timeframe, liqState)

	doneSession := w.telemetry.Time(telemetry.PhaseSession, symbolLabel, tfLabel)
	sessionState := session.Update(candles, w.settings.Session)
	doneSession()
	w.state.Session.Set(event.Timeframe, sessionState)

	doneFib := w.telemetry.Time(telemetry.PhaseFib, symbolLabel, tfLabel)
	fibState := fib.Update(candles, structState.Swings, w.settings.Fib)
	doneFib()
	w.state.Fib.Set(event.Timeframe, fibState)

	doneCtx := w.telemetry.Time(telemetry.PhaseContext, symbolLabel, tfLabel)
	w.rebuildContext()
	doneCtx()

	doneSnap := w.telemetry.Time(telemetry.PhaseSnapshot, symbolLabel, tfLabel)
	snap := SnapshotFrom(w.state, w.settings, event.Candle.Time)
	doneSnap()
	return snap, result, nil
}

// Snapshot returns the current AnalysisSnapshot without applying a new
// event — also mutex-guarded, so it never observes a half-updated state
// mid-Apply.
func (w *SymbolWorker) Snapshot(now int64) AnalysisSnapshot {
	w.mu.Lock()
	defer w.mu.Unlock()
	return SnapshotFrom(w.state, w.settings, now)
}

func (w *SymbolWorker) rebuildContext() {
	perTF := make(map[market.Timeframe]context.TimeframeInput, len(w.state.History.Timeframes))
	for tf := range w.state.History.Timeframes {
		structState, sok := w.state.Structure.Get(tf)
		liqState, lok := w.state.Liquidity.Get(tf)
		if !sok || !lok {
			continue // no bar has closed for this timeframe yet
		}
		atrSeries := w.state.Measurements.ATR(tf)
		var lastATR float64
		if len(atrSeries) > 0 {
			lastATR = atrSeries[len(atrSeries)-1]
		}
		zoneState, _ := w.state.Zone.Get(tf)
		sessionState, _ := w.state.Session.Get(tf)
		fibState, _ := w.state.Fib.Get(tf)
		perTF[tf] = context.TimeframeInput{Structure: structState, Liquidity: liqState, Zones: zoneState, Session: sessionState, Fib: fibState, ATR: lastATR}
	}
	w.state.Context = context.Build(w.state.Symbol, w.settings.PrimaryTimeframe, perTF)
}
