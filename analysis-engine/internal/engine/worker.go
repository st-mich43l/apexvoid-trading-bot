package engine

import (
	"sync"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/state"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
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
	registry  *strategy.Registry
	publisher *OpportunityPublisher // nil = no Kafka opportunity publication (Phase S9)
	algo      kafka.AlgorithmVersion
}

// NewSymbolWorker returns a worker for symbol with an empty SymbolState
// bounded per settings.HistoryDepths. Phase S8: also constructs this
// symbol's own strategy.Registry from settings.Strategies and this
// module's fixed strategyFactories composition root (strategies.go) —
// once, here, not on every Apply, since a Registry's constructed
// Strategy instances are immutable after New (no per-symbol mutable
// state lives on them; see e.g. supply.Strategy's own fields), so
// re-evaluating the same Registry concurrently across events for THIS
// symbol is safe under Apply's own mutex, and a distinct Registry per
// worker keeps a future per-symbol config override (not implemented
// today, see Engine.Register's doc comment) trivial to support without
// restructuring this constructor.
func NewSymbolWorker(symbol market.Symbol, settings Settings, recorder *telemetry.Recorder, publisher *OpportunityPublisher) (*SymbolWorker, error) {
	ws, err := state.NewSymbolState(symbol, settings.HistoryDepths, settings.AllowReplaceForming)
	if err != nil {
		return nil, err
	}
	registry, err := strategy.NewRegistry(settings.Strategies, strategyFactories)
	if err != nil {
		return nil, err
	}
	if recorder == nil {
		recorder = telemetry.NewRecorder()
	}
	algo := kafka.AlgorithmVersion{Structure: settings.Structure.Version, Liquidity: settings.Liquidity.Version}
	return &SymbolWorker{state: ws, settings: settings, telemetry: recorder, registry: registry, publisher: publisher, algo: algo}, nil
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
	if event.PublishesOpportunity() {
		w.publisher.ResumeLive(w.state.Symbol)
	}

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

	doneTrend := w.telemetry.Time(telemetry.PhaseTrendline, symbolLabel, tfLabel)
	trendState := trendline.Update(candles, atrSeries, structState.Swings, w.settings.Trendline)
	doneTrend()
	w.state.Trendline.Set(event.Timeframe, trendState)
	doneKeyLevel := w.telemetry.Time(telemetry.PhaseKeyLevel, symbolLabel, tfLabel)
	keyLevelState := keylevel.Update(candles, atrSeries, structState.Swings, w.settings.KeyLevel)
	doneKeyLevel()
	w.state.KeyLevel.Set(event.Timeframe, keyLevelState)
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

	// Phase S8: run every enabled strategy that declared event.Timeframe
	// as a dependency against the just-rebuilt canonical context, then
	// feed whatever Candidates it returns through this symbol's own
	// Opportunity lifecycle. A Registry/Book error here is a real
	// correctness bug (a misbehaving strategy, a lifecycle contract
	// violation) — never a routine business outcome — so it aborts this
	// Apply the same way an indicator.CanonicalATR error already does
	// above, rather than silently swallowing it.
	doneStrat := w.telemetry.Time(telemetry.PhaseStrategy, symbolLabel, tfLabel)
	evaluation, evalErr := w.registry.Evaluate(&w.state.Context, event.Timeframe)
	doneStrat()
	if evalErr != nil {
		return AnalysisSnapshot{}, result, evalErr
	}

	doneOpp := w.telemetry.Time(telemetry.PhaseOpportunity, symbolLabel, tfLabel)
	for i := range evaluation.Candidates {
		// Overwrite the strategy's own narrower per-strategy-parameters
		// placeholder (see e.g. supply.configFingerprint's doc comment)
		// with the real whole-resolved-document provenance — this is the
		// enrichment Phase S7's own strategies documented Phase S8 as
		// expected to perform, now that engine (which alone reaches
		// *config.Document) is the one applying it.
		evaluation.Candidates[i].Provenance.ConfigVersion = w.settings.ConfigVersion
		evaluation.Candidates[i].Provenance.ConfigFingerprint = w.settings.ConfigFingerprint
		candidate, timingErr := opportunity.AtFirstObservation(evaluation.Candidates[i], event.Candle.Time)
		if timingErr != nil {
			doneOpp()
			return AnalysisSnapshot{}, result, timingErr
		}
		candidate.ObservedTimeframe = event.Timeframe
		candidate.Technical = w.technicalContext(event)
		observed, obsErr := w.state.Opportunities.Observe(candidate, event.Candle.Time)
		if obsErr != nil {
			doneOpp()
			return AnalysisSnapshot{}, result, obsErr
		}
		w.observeTransition(observed, symbolLabel, tfLabel, event.PublishesOpportunity())
	}
	for _, expired := range w.state.Opportunities.Expire(event.Candle.Time) {
		w.observeTransition(expired, symbolLabel, tfLabel, event.PublishesOpportunity())
	}
	doneOpp()

	doneSnap := w.telemetry.Time(telemetry.PhaseSnapshot, symbolLabel, tfLabel)
	snap := SnapshotFrom(w.state, w.settings, event.Candle.Time)
	doneSnap()
	return snap, result, nil
}

// observeTransition records one opportunity lifecycle transition under
// its own counter (TransitionNoop is deliberately uncounted — see
// telemetry.CounterOpportunitiesCreated's doc comment) and, for a
// publishable transition (source task §81: Created/Invalidated/Expired —
// Transition.ShouldPublish()), hands it to the shared OpportunityPublisher
// for background Kafka delivery (Phase S9). Enqueue is a fast, lock-only
// append — see OpportunityPublisher's own doc comment for why the actual
// network call never happens on this path — and is a no-op on a nil
// w.publisher (Kafka disabled/absent).
func (w *SymbolWorker) observeTransition(t opportunity.Transition, symbol, timeframe string, publish bool) {
	switch t.Kind {
	case opportunity.TransitionCreated:
		w.telemetry.Count(telemetry.CounterOpportunitiesCreated, symbol, timeframe, 1)
	case opportunity.TransitionActivated:
		w.telemetry.Count(telemetry.CounterOpportunitiesActivated, symbol, timeframe, 1)
	case opportunity.TransitionDuplicate:
		w.telemetry.Count(telemetry.CounterOpportunitiesDuplicate, symbol, timeframe, 1)
	case opportunity.TransitionInvalidated:
		w.telemetry.Count(telemetry.CounterOpportunitiesInvalidated, symbol, timeframe, 1)
	case opportunity.TransitionExpired:
		w.telemetry.Count(telemetry.CounterOpportunitiesExpired, symbol, timeframe, 1)
	}
	w.publisher.Observe(w.state.Symbol, w.algo, t, publish)
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
		trendState, _ := w.state.Trendline.Get(tf)
		keyLevelState, _ := w.state.KeyLevel.Get(tf)
		sessionState, _ := w.state.Session.Get(tf)
		fibState, _ := w.state.Fib.Get(tf)
		perTF[tf] = context.TimeframeInput{
			Candles: w.state.History.For(tf).Snapshot(), Structure: structState, Liquidity: liqState, Zones: zoneState,
			Trendline: trendState, KeyLevel: keyLevelState, Session: sessionState, Fib: fibState, ATR: lastATR,
		}
	}
	w.state.Context = context.Build(w.state.Symbol, w.settings.PrimaryTimeframe, perTF)
}

// technicalContext assembles the policy-input facts for the bar that just
// closed (S13B): the canonical ATR of that bar's own timeframe, that bar's
// close as the geometric reference, and the engine's structural bias. Returns
// nil — never a placeholder — when the ATR series is not yet available, so a
// consumer sees "unavailable" and fails closed instead of guessing.
func (w *SymbolWorker) technicalContext(event marketdata.BarEvent) *opportunity.TechnicalContext {
	atrSeries := w.state.Measurements.ATR(event.Timeframe)
	if len(atrSeries) == 0 {
		return nil
	}
	atr := atrSeries[len(atrSeries)-1]
	if !(atr > 0) || !(event.Candle.Close > 0) {
		return nil
	}
	facts := &opportunity.TechnicalContext{
		ATR: atr, ReferencePrice: event.Candle.Close, ReferenceTime: event.Candle.Time,
	}
	if bias := w.state.Context.Bias; bias.Direction.IsValid() {
		facts.BiasDirection = bias.Direction
		facts.BiasLayer = bias.Layer.String()
	}
	facts.HigherTimeframes = ClosedHigherTimeframeBiases(&w.state.Context, event.Timeframe, event.Candle.Time)

	return facts
}
