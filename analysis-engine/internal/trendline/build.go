package trendline

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// eps mirrors trendline_v2.py's own _EPS — a floating-point slack used
// throughout the same comparisons Python makes it against.
const eps = 1e-12

// Update is this domain's engine-pipeline entrypoint: Build wrapped in
// TrendlineState. Pure and causal, same contract as
// structure.Update/zone.Update/fib.Update/keylevel.Update.
func Update(candles []market.Candle, atrSeries []float64, swings []structure.Swing, cfg Config) TrendlineState {
	return TrendlineState{Lines: Build(candles, atrSeries, swings, cfg)}
}

// Build ports build_causal_trendlines: chronologically ADJACENT
// confirmed same-kind pivot pairs become anchor candidates (see doc.go —
// never a later, non-adjacent re-pairing), filtered by slope direction
// and min/max-slope-ATR, validated forward against later pivots, then
// deduplicated by projected-price-and-slope proximity.
func Build(candles []market.Candle, atrSeries []float64, swings []structure.Swing, cfg Config) []Trendline {
	if len(candles) == 0 {
		return nil
	}
	atr := medianATR(atrSeries)
	if !isFinite(atr) || atr <= eps {
		return nil
	}
	settings := resolveSettings(cfg, atr)
	lastBar := len(candles) - 1

	var candidates []Trendline
	for _, pass := range [...]struct {
		swingKind structure.PivotKind
		lineKind  Kind
	}{
		{structure.SwingLow, KindSupport},
		{structure.SwingHigh, KindResistance},
	} {
		points := confirmedPoints(swings, candles, pass.swingKind)
		for i := 0; i+1 < len(points); i++ {
			a, b := points[i], points[i+1]
			if b.index-a.index < settings.minimumSpacing {
				continue
			}
			if line, ok := candidateFromAnchors(pass.lineKind, a, b, points, candles, atr, settings); ok {
				candidates = append(candidates, line)
			}
		}
	}
	return deduplicate(candidates, lastBar, atr, settings)
}

// pivot is trendline_v2.py's own _Pivot — a confirmed swing usable as an
// anchor or validation candidate.
type pivot struct {
	index          int
	price          float64
	confirmedIndex int
	swingID        string
}

// confirmedPoints ports _confirmed_points: swings of the given kind,
// resolved to their candle index and confirmed-index (see doc.go on why
// this port skips rather than falls back when ConfirmedAt cannot be
// located — structure.Swing.ConfirmedAt is always populated here, unlike
// Python's optional cached-payload case), sorted ascending by index.
// Python also dedups by (index, price, confirmed_index) tuple identity
// before sorting — omitted here: structure.Update is this package's sole
// swings producer and already returns a clean, unique list, unlike
// Python's swings list which can be assembled from multiple merged
// sources upstream.
func confirmedPoints(swings []structure.Swing, candles []market.Candle, kind structure.PivotKind) []pivot {
	lastBar := len(candles) - 1
	var points []pivot
	for _, s := range swings {
		if s.Kind != kind {
			continue
		}
		price := float64(s.Price)
		if !isFinite(price) {
			continue
		}
		idx := indexOfTime(candles, s.Time)
		if idx < 0 {
			continue
		}
		confirmedIdx := indexOfTime(candles, s.ConfirmedAt)
		if confirmedIdx < 0 || confirmedIdx > lastBar {
			continue
		}
		points = append(points, pivot{index: idx, price: price, confirmedIndex: confirmedIdx, swingID: s.ID})
	}
	sort.Slice(points, func(i, j int) bool { return points[i].index < points[j].index })
	return points
}

// indexOfTime finds t's exact position in Time-ascending candles, or -1.
func indexOfTime(candles []market.Candle, t int64) int {
	lo, hi := 0, len(candles)
	for lo < hi {
		mid := (lo + hi) / 2
		if candles[mid].Time < t {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	if lo < len(candles) && candles[lo].Time == t {
		return lo
	}
	return -1
}

// candidateFromAnchors ports _candidate_from_anchors.
func candidateFromAnchors(kind Kind, a, b pivot, points []pivot, candles []market.Candle, atr float64, settings resolvedSettings) (Trendline, bool) {
	slope := (b.price - a.price) / float64(b.index-a.index)
	if kind == KindSupport && slope < settings.minimumSlope-eps {
		return Trendline{}, false
	}
	if kind == KindResistance && slope > -settings.minimumSlope+eps {
		return Trendline{}, false
	}
	if math.Abs(slope) > settings.maximumSlope+eps {
		return Trendline{}, false
	}
	intercept := a.price - slope*float64(a.index)

	var validations []ValidationTouch
	var validationIndexes []int
	previousInteraction := b.index
	for _, p := range points {
		if p.index <= b.index || p.confirmedIndex >= len(candles) {
			continue
		}
		if p.index-previousInteraction < settings.minimumValidationSpacing {
			continue
		}
		touch, ok := measureValidation(kind, p, slope, intercept, candles, atr, settings)
		if !ok {
			continue
		}
		validations = append(validations, touch)
		validationIndexes = append(validationIndexes, p.index)
		previousInteraction = p.index
	}

	lastTouch := b.index
	if n := len(validationIndexes); n > 0 {
		lastTouch = validationIndexes[n-1]
	}
	spanBars := lastTouch - a.index

	hp := computeHealth(kind, slope, intercept, candles, settings, b.index+1)

	validationCount := len(validations)
	exhausted := validationCount >= settings.exhaustionValidations
	state := hp.state
	if state != StateBroken && state != StateDegraded {
		switch {
		case exhausted:
			state = StateExhausted
		case validationCount >= settings.minimumValidations && spanBars >= settings.minimumSpan:
			state = StateConfirmed
		default:
			state = StateTentative
		}
	}

	var brokenAt *int64
	if hp.breakIndex != nil {
		t := candles[*hp.breakIndex].Time
		brokenAt = &t
	}

	return Trendline{
		Kind:               kind,
		AnchorA:            a.swingID,
		AnchorB:            b.swingID,
		AnchorAIndex:       a.index,
		AnchorBIndex:       b.index,
		Slope:              slope,
		Intercept:          intercept,
		SlopeATRPerBar:     slope / atr,
		State:              state,
		ValidationTouches:  validations,
		WickViolations:     hp.wickViolations,
		CloseViolations:    hp.closeViolations,
		BrokenAt:           brokenAt,
		ViolationReclaimed: hp.violationReclaimed,
		SpanBars:           spanBars,
		Exhausted:          exhausted,
	}, true
}

// measureValidation ports _measure_validation: a candidate touch only
// becomes a real ValidationTouch once its own reaction window (reaction
// bars after confirmation) has fully closed — a partial window is
// deferred, never counted early (the core causality guard).
func measureValidation(kind Kind, p pivot, slope, intercept float64, candles []market.Candle, atr float64, settings resolvedSettings) (ValidationTouch, bool) {
	lineAtPivot := slope*float64(p.index) + intercept
	errAbs := math.Abs(p.price - lineAtPivot)
	if errAbs > settings.validationTolerance+eps {
		return ValidationTouch{}, false
	}
	reactionEnd := p.confirmedIndex + settings.validationReactionBars
	if reactionEnd >= len(candles) {
		return ValidationTouch{}, false
	}

	var penetration, favorable float64
	if kind == KindSupport {
		maxPen := 0.0
		for idx := p.index; idx <= reactionEnd; idx++ {
			expected := slope*float64(idx) + intercept
			if d := expected - float64(candles[idx].Low); d > maxPen {
				maxPen = d
			}
		}
		penetration = maxF(0, maxPen)
		highMax := candles[p.index].High
		for idx := p.index + 1; idx <= reactionEnd; idx++ {
			if candles[idx].High > highMax {
				highMax = candles[idx].High
			}
		}
		favorable = maxF(float64(highMax)-lineAtPivot, 0)
	} else {
		maxPen := 0.0
		for idx := p.index; idx <= reactionEnd; idx++ {
			expected := slope*float64(idx) + intercept
			if d := float64(candles[idx].High) - expected; d > maxPen {
				maxPen = d
			}
		}
		penetration = maxF(0, maxPen)
		lowMin := candles[p.index].Low
		for idx := p.index + 1; idx <= reactionEnd; idx++ {
			if candles[idx].Low < lowMin {
				lowMin = candles[idx].Low
			}
		}
		favorable = maxF(lineAtPivot-float64(lowMin), 0)
	}
	adverse := penetration

	closeLine := slope*float64(reactionEnd) + intercept
	closeDistance := sideDistance(kind, float64(candles[reactionEnd].Close), closeLine)

	priorDistance := 0.0
	approachValid := false
	if p.index > 0 {
		priorLine := slope*float64(p.index-1) + intercept
		priorDistance = sideDistance(kind, float64(candles[p.index-1].Close), priorLine)
		approachValid = priorDistance >= settings.approachMinDistance
	}
	reclaimed := closeDistance >= 0
	structureConfirmed := approachValid && reclaimed &&
		penetration <= settings.invalidationPenetration+eps &&
		favorable >= settings.minimumFavorableExcursion-eps
	if !structureConfirmed {
		return ValidationTouch{}, false
	}

	reactionBars := reactionEnd - p.index + 1
	strength := minF(1.0, (favorable/atr)+maxF(0, closeDistance/atr)-(penetration/atr))

	return ValidationTouch{
		BarIndex:                p.index,
		Time:                    candles[p.index].Time,
		ProjectedLinePrice:      market.Price(lineAtPivot),
		ActualExtreme:           market.Price(p.price),
		TouchErrorPrice:         errAbs,
		TouchErrorATR:           errAbs / atr,
		PenetrationPrice:        penetration,
		PenetrationATR:          penetration / atr,
		CloseDistanceFromLine:   closeDistance,
		CloseDistanceATR:        closeDistance / atr,
		FavorableExcursionPrice: favorable,
		FavorableExcursionATR:   favorable / atr,
		AdverseExcursionPrice:   adverse,
		AdverseExcursionATR:     adverse / atr,
		ReactionBars:            reactionBars,
		Reclaimed:               reclaimed,
		RejectionStrength:       strength,
		StructureConfirmed:      structureConfirmed,
		ApproachDirectionValid:  approachValid,
	}, true
}

// deduplicate ports _deduplicate/_near_duplicate: near-identical
// projections are one structural line. Ranked oldest-anchor-first (a
// tie broken by MORE validations first) so the oldest anchor pair is
// always kept — never let a later pairing silently erase an established
// line's validation/violation history.
func deduplicate(lines []Trendline, lastBar int, atr float64, settings resolvedSettings) []Trendline {
	ranked := append([]Trendline(nil), lines...)
	sort.SliceStable(ranked, func(i, j int) bool {
		li, lj := ranked[i], ranked[j]
		if li.AnchorAIndex != lj.AnchorAIndex {
			return li.AnchorAIndex < lj.AnchorAIndex
		}
		if li.AnchorBIndex != lj.AnchorBIndex {
			return li.AnchorBIndex < lj.AnchorBIndex
		}
		return len(li.ValidationTouches) > len(lj.ValidationTouches)
	})

	var kept []Trendline
	for _, line := range ranked {
		dup := false
		for _, other := range kept {
			if nearDuplicate(line, other, lastBar, atr, settings) {
				dup = true
				break
			}
		}
		if !dup {
			kept = append(kept, line)
		}
	}

	sort.SliceStable(kept, func(i, j int) bool {
		a, b := kept[i], kept[j]
		if a.Kind != b.Kind {
			return a.Kind < b.Kind
		}
		va, vb := ValueAt(a, lastBar), ValueAt(b, lastBar)
		if va != vb {
			return va < vb
		}
		return a.Slope < b.Slope
	})
	return kept
}

func nearDuplicate(a, b Trendline, lastBar int, atr float64, settings resolvedSettings) bool {
	if a.Kind != b.Kind {
		return false
	}
	if math.Abs(float64(ValueAt(a, lastBar)-ValueAt(b, lastBar))) > settings.dedupValueATR*atr {
		return false
	}
	denom := maxF(math.Abs(a.Slope), maxF(math.Abs(b.Slope), eps))
	return math.Abs(a.Slope-b.Slope)/denom <= settings.dedupSlopePercent+eps
}

// resolvedSettings is _settings()'s own ATR-scaled dict, computed once
// per Build call.
type resolvedSettings struct {
	minimumSlope              float64
	maximumSlope              float64
	minimumSpacing            int
	minimumSpan               int
	minimumValidationSpacing  int
	validationTolerance       float64
	invalidationPenetration   float64
	closeViolation            float64
	approachMinDistance       float64
	minimumFavorableExcursion float64
	validationReactionBars    int
	minimumValidations        int
	exhaustionValidations     int
	maximumWickViolations     int
	dedupValueATR             float64
	dedupSlopePercent         float64
}

func resolveSettings(cfg Config, atr float64) resolvedSettings {
	return resolvedSettings{
		minimumSlope:              maxF(0, cfg.MinimumSlopeATR) * atr,
		maximumSlope:              maxF(0, cfg.MaximumSlopeATR) * atr,
		minimumSpacing:            maxI(1, cfg.MinimumTouchSpacingBars),
		minimumSpan:               maxI(1, cfg.MinimumSpanBars),
		minimumValidationSpacing:  maxI(1, cfg.MinimumValidationTouchSpacingBars),
		validationTolerance:       maxF(0, cfg.ValidationTouchToleranceATR) * atr,
		invalidationPenetration:   maxF(0, cfg.InvalidationPenetrationATR) * atr,
		closeViolation:            maxF(0, cfg.CloseViolationATR) * atr,
		approachMinDistance:       maxF(0, cfg.ApproachMinDistanceATR) * atr,
		minimumFavorableExcursion: maxF(0, cfg.MinimumValidationFavorableExcursionATR) * atr,
		validationReactionBars:    maxI(0, cfg.ValidationReactionBars),
		minimumValidations:        maxI(1, cfg.MinimumValidationTouches),
		exhaustionValidations:     maxI(1, cfg.ExhaustionValidationTouches),
		maximumWickViolations:     maxI(0, cfg.MaximumWickViolations),
		dedupValueATR:             maxF(0, cfg.DedupValueATR),
		dedupSlopePercent:         maxF(0, cfg.DedupSlopePercent),
	}
}

// medianATR ports atr_scalar's series branch exactly: only NaN values
// are dropped before taking the median (matching pandas' own dropna(),
// which does not touch Inf or non-positive values) — the finite/positive
// check applies to the FINAL median only, not per element, same as
// Python's own two-stage validation.
func medianATR(series []float64) float64 {
	const fallback = 1.0
	clean := make([]float64, 0, len(series))
	for _, v := range series {
		if !math.IsNaN(v) {
			clean = append(clean, v)
		}
	}
	if len(clean) == 0 {
		return fallback
	}
	sort.Float64s(clean)
	n := len(clean)
	var median float64
	if n%2 == 1 {
		median = clean[n/2]
	} else {
		median = (clean[n/2-1] + clean[n/2]) / 2
	}
	if !isFinite(median) || median <= 0 {
		return fallback
	}
	return median
}

func isFinite(v float64) bool {
	return !math.IsNaN(v) && !math.IsInf(v, 0)
}

func maxF(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func minF(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

func maxI(a, b int) int {
	if a > b {
		return a
	}
	return b
}
