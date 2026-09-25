package trendline

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// State is a Trendline's lifecycle stage — trendline_v2.py's own
// 6-value state machine (Python's `_Health.state`/final `Trendline.state`
// strings), genuinely different from zone.State's 5-value one (Fresh/
// Touched/PartiallyMitigated/Mitigated/Invalidated) — not forced to
// match.
//
// StateCandidate is a real value _health() can compute, but — a
// confirmed property of the ported Python control flow, not a gap in
// this port — candidateFromAnchors always overrides it with one of
// StateExhausted/StateConfirmed/StateTentative whenever health's state
// is NOT StateBroken/StateDegraded. A Trendline this package returns
// therefore never actually carries StateCandidate; the value exists so
// the enum names the complete conceptual state space Python's own
// _Health/Trendline pair describes, matching this domain's own plan.
type State uint8

const (
	StateCandidate State = iota
	StateTentative
	StateConfirmed
	StateExhausted
	StateDegraded
	StateBroken
)

func (s State) String() string {
	switch s {
	case StateTentative:
		return "tentative"
	case StateConfirmed:
		return "confirmed"
	case StateExhausted:
		return "exhausted"
	case StateDegraded:
		return "degraded"
	case StateBroken:
		return "broken"
	default:
		return "candidate"
	}
}

// health is _Health: the bar-by-bar violation scan candidateFromAnchors
// derives a line's base State from, before validation-count/span
// promotion is applied on top.
type health struct {
	state State

	broken     bool
	breakIndex *int

	wickViolations  int
	closeViolations int

	maxPenetration    float64
	latestPenetration float64

	violationIndex        *int
	violationReclaimed    bool
	consecutiveViolations int
}

// computeHealth ports _health: scans every bar from start to the end of
// candles (NOT just pivot points — every bar) for wick penetration past
// invalidationPenetration and close violations past closeViolation.
//
// "Broken" is defined by an UNRESOLVED close violation — a close beyond
// the invalidated side that no later bar's close reclaims — never by a
// wick alone, however deep. A wick violation that later closes back on
// the valid side marks violationReclaimed but does not itself break the
// line; only a close violation can set/clear the broken state, and only
// the LATEST unresolved one determines BrokenAt.
func computeHealth(kind Kind, slope, intercept float64, candles []market.Candle, settings resolvedSettings, start int) health {
	wickViolations := 0
	closeViolations := 0
	maxPenetration := 0.0
	latestPenetration := 0.0
	var violationIndex *int
	var unresolvedClose *int
	recovered := false
	consecutive := 0
	maxConsecutive := 0

	from := start
	if from < 0 {
		from = 0
	}
	for idx := from; idx < len(candles); idx++ {
		line := slope*float64(idx) + intercept
		c := candles[idx]

		var penetration float64
		if kind == KindSupport {
			penetration = maxF(0, line-float64(c.Low))
		} else {
			penetration = maxF(0, float64(c.High)-line)
		}
		closeDistance := sideDistance(kind, float64(c.Close), line)
		if penetration > maxPenetration {
			maxPenetration = penetration
		}

		if penetration > settings.invalidationPenetration+eps {
			wickViolations++
			latestPenetration = penetration
			i := idx
			violationIndex = &i
			consecutive++
			if consecutive > maxConsecutive {
				maxConsecutive = consecutive
			}
			if closeDistance >= 0 {
				recovered = true
			}
		} else {
			consecutive = 0
		}

		if closeDistance < -settings.closeViolation-eps {
			closeViolations++
			i := idx
			unresolvedClose = &i
			if -closeDistance > latestPenetration {
				latestPenetration = -closeDistance
			}
			violationIndex = &i
			recovered = false
		} else if unresolvedClose != nil && closeDistance >= 0 {
			recovered = true
			unresolvedClose = nil
		}
	}

	broken := unresolvedClose != nil
	var st State
	switch {
	case broken:
		st = StateBroken
	case wickViolations > settings.maximumWickViolations:
		st = StateDegraded
	case closeViolations > 0:
		st = StateDegraded
	default:
		st = StateCandidate
	}
	return health{
		state: st, broken: broken, breakIndex: unresolvedClose,
		wickViolations: wickViolations, closeViolations: closeViolations,
		maxPenetration: maxPenetration, latestPenetration: latestPenetration,
		violationIndex: violationIndex, violationReclaimed: recovered,
		consecutiveViolations: maxConsecutive,
	}
}

// sideDistance ports _side_distance: positive means price is on the
// line's valid side (above for support, below for resistance).
func sideDistance(kind Kind, price, line float64) float64 {
	if kind == KindSupport {
		return price - line
	}
	return line - price
}
