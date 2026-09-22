package structure

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// StructureState is the complete structural read for one symbol+timeframe
// at one point in time — source task §17. Book (below) is what
// internal/state.SymbolState actually holds; StructureState is the
// (immutable, once returned) result Update produces fresh each call.
type StructureState struct {
	Micro        LayerState
	Internal     LayerState
	Intermediate LayerState
	Major        LayerState

	Swings []Swing
	Breaks []StructureBreak
}

// Update is Structure V2's single entrypoint: pivots -> promoted swings ->
// per-layer trend/protected-level state -> the most recent break (if any)
// of each layer's current protected level, classified BOS/CHoCH/none.
// Pure and causal: only ever reads candles/atrSeries given to it (source
// task §10 — "at timestamp T, analysis may use data <= T"), so calling
// Update(candles[:T], atrSeries[:T], ...) can never see anything from
// candles[T:] — proven, not just claimed, in
// test/structure/causality_test.go.
//
// candles/atrSeries must already be the narrow CALCULATION window a
// caller actually needs (internal/marketdata.TimeframeHistory.LatestN),
// not necessarily the full stored history — source task §6. Both slices
// must be index-aligned and the same length; atrSeries must come from
// indicator.CanonicalATR, never an independently-chosen formula (§27).
//
// Scope note (recorded in docs/analysis-engine-v2-migration.md, not
// hidden here): break detection re-scans the full given candle window
// against each layer's CURRENT (fully-formed) protected level, rather
// than interleaving break detection bar-by-bar as the trend read itself
// evolves. A genuinely incremental, event-interleaved version (detecting
// a CHoCH the moment it happens and re-deriving Trend from that exact
// moment forward) is a real, deliberate v2.1 scope decision, not
// implemented in this pass.
func Update(candles []market.Candle, atrSeries []float64, tf market.Timeframe, settings Settings) StructureState {
	pivots := DetectPivots(candles, settings.PivotLeftBars, settings.PivotRightBars, atrSeries)

	swings := make([]Swing, 0, len(pivots))
	for _, p := range pivots {
		if sw, ok := PromoteSwing(p, tf, settings.PivotRightBars, settings.Promotion); ok {
			swings = append(swings, sw)
		}
	}

	state := StructureState{Swings: swings}
	state.Micro = BuildLayerState(StructureMicro, swings, settings.EqualToleranceATR)
	state.Internal = BuildLayerState(StructureInternal, swings, settings.EqualToleranceATR)
	state.Intermediate = BuildLayerState(StructureIntermediate, swings, settings.EqualToleranceATR)
	state.Major = BuildLayerState(StructureMajor, swings, settings.EqualToleranceATR)

	for _, layer := range []*LayerState{&state.Micro, &state.Internal, &state.Intermediate, &state.Major} {
		protected := currentProtectedLevel(*layer)
		if protected == nil {
			continue
		}
		fromIndex := indexAtOrAfter(candles, protected.ConfirmedAt)
		if fromIndex < 0 {
			continue
		}
		brk := DetectBreak(candles, fromIndex, *protected, atrSeries, settings.Break)
		if brk == nil {
			continue
		}
		brk.Event = ClassifyEvent(*brk, *layer)
		layer.LastBreak = brk
		state.Breaks = append(state.Breaks, *brk)
	}
	return state
}

func currentProtectedLevel(layer LayerState) *Swing {
	switch layer.Trend {
	case TrendBullish:
		return layer.ProtectedLow
	case TrendBearish:
		return layer.ProtectedHigh
	default:
		return nil
	}
}

// indexAtOrAfter returns the index of the first candle whose Time >= t,
// or -1 if none. candles are assumed Time-ascending (marketdata.TimeframeHistory's
// own invariant).
func indexAtOrAfter(candles []market.Candle, t int64) int {
	lo, hi := 0, len(candles)
	for lo < hi {
		mid := (lo + hi) / 2
		if candles[mid].Time < t {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	if lo == len(candles) {
		return -1
	}
	return lo
}
