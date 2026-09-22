package structure

// TrendState is one layer's directional read — source task §17.
type TrendState uint8

const (
	TrendUnknown TrendState = iota
	TrendBullish
	TrendBearish
	TrendRange
)

func (t TrendState) String() string {
	switch t {
	case TrendBullish:
		return "bullish"
	case TrendBearish:
		return "bearish"
	case TrendRange:
		return "range"
	default:
		return "unknown"
	}
}

// LayerState is one structural layer's running read — source task §17.
// ProtectedLow/ProtectedHigh are the levels whose failure would damage the
// currently-established Trend at this layer (§23); LastBreak is filled by
// the caller (structure.go's Update) once a break of the protected level
// is found — BuildLayerState itself only derives Trend/LastHigh/LastLow/
// Protected from the swing sequence, not from any break.
type LayerState struct {
	Layer StructureLayer

	Trend TrendState

	LastHigh *Swing
	LastLow  *Swing

	ProtectedHigh *Swing
	ProtectedLow  *Swing

	LastBreak *StructureBreak
}

// BuildLayerState folds an already-time-ordered (by Time, ascending — the
// order DetectPivots/PromoteSwing naturally produce) sequence of same-
// layer swings into the layer's running trend read: two consecutive
// Higher-High + Higher-Low classifications establish TrendBullish (and
// symmetrically TrendBearish); anything else (a mixed HH+LL, LH+HL, or
// insufficient history) is TrendRange rather than guessed — source task
// §38's "do not rely only on one indicator" applied narrowly here as "do
// not call a trend from a single ambiguous swing." The protected level
// persists through a Range/Unknown read rather than being cleared — the
// last established bullish protected low remains the level to watch even
// while the market pauses, until a new trend read (bullish OR bearish)
// explicitly supersedes it. swings not belonging to layer are ignored,
// so callers may pass the full StructureState.Swings slice directly.
//
// equalTolerance is the ATR-units equal-high/equal-low band (config/
// analysis.yml's analysis.structure.equal_level.tolerance_atr) — an
// explicit parameter, not package state, so this function stays safe to
// call concurrently for different symbols (source task §41: "different
// symbols = concurrent").
func BuildLayerState(layer StructureLayer, swings []Swing, equalTolerance float64) LayerState {
	state := LayerState{Layer: layer}
	var highRelation, lowRelation SwingRelation

	for i := range swings {
		s := swings[i]
		if s.Layer != layer {
			continue
		}
		if s.Kind == SwingHigh {
			if state.LastHigh != nil {
				highRelation = ClassifySwingRelation(s, *state.LastHigh, referenceATR(s), equalTolerance)
			}
			swingCopy := s
			state.LastHigh = &swingCopy
		} else {
			if state.LastLow != nil {
				lowRelation = ClassifySwingRelation(s, *state.LastLow, referenceATR(s), equalTolerance)
			}
			swingCopy := s
			state.LastLow = &swingCopy
		}

		state.Trend = deriveTrend(highRelation, lowRelation)
		switch state.Trend {
		case TrendBullish:
			state.ProtectedLow = state.LastLow
		case TrendBearish:
			state.ProtectedHigh = state.LastHigh
		}
	}
	return state
}

// referenceATR recovers the ATR value a swing's own ExcursionATR was
// normalized against (ExcursionPrice / ExcursionATR), so
// ClassifySwingRelation can express its equality band in the same ATR
// units the swing's own significance was measured in, without
// BuildLayerState needing a separate candle/ATR-series parameter.
func referenceATR(s Swing) float64 {
	if s.ExcursionATR <= 0 {
		return 0
	}
	return s.ExcursionPrice / s.ExcursionATR
}

// deriveTrend applies source task §17's rule: Bullish needs the most
// recently classified high AND low relation to both agree bullish (HH +
// HL — the two need not have been classified on the same swing, only both
// still be the latest known reading for their own side); Bearish
// symmetric (LH + LL); anything else is Range — including the case where
// one side hasn't produced a second same-kind swing yet to classify
// (RelationUnknown), which must never be silently treated as continuing
// whatever the previous trend was.
func deriveTrend(highRelation, lowRelation SwingRelation) TrendState {
	bullish := highRelation == HigherHigh && lowRelation == HigherLow
	bearish := highRelation == LowerHigh && lowRelation == LowerLow
	switch {
	case bullish:
		return TrendBullish
	case bearish:
		return TrendBearish
	default:
		return TrendRange
	}
}
