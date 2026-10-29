package strategyutil

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Level episodes: the two sequences a horizontal price level can host, read from closed candles only.
//
//	sweep -> reclaim -> structure shift   (a stop run through the level that is taken back: FindLevelSweeps)
//	break -> acceptance -> retest -> rejection (a break that holds and is retested from the new side: FindLevelRetest)
//
// Both are written in the BULLISH orientation (the level is support: the sweep goes below it, the break goes above it, the trade is a BUY).
// A SELL is the same sequence on market.Mirror-ed candles and a mirrored level; the caller reflects the result back. Nothing here reads a candle
// after the last one it is given, and every window is measured in candle open times, never in slice positions of another timeframe.

// Rejection reasons of the shared episodes. They are the stable vocabulary the strategies' replay reports use.
const (
	LevelReasonSweepTooDeep            = "sweep_too_deep"
	LevelReasonAccepted                = "accepted_not_swept"
	LevelReasonReclaimExpired          = "reclaim_window_expired"
	LevelReasonReclaimLost             = "reclaim_lost"
	LevelReasonNoStructure             = "no_structure_reference"
	LevelReasonMSSExpired              = "mss_window_expired"
	LevelReasonMSSQuality              = "mss_quality_insufficient"
	LevelReasonATRUnavailable          = "atr_unavailable"
	LevelReasonBreakFailed             = "break_failed"
	LevelReasonRetestNotRejected       = "retest_not_rejected"
	LevelReasonStaleRetest             = "stale_retest"
	LevelReasonNoRetest                = "no_retest"
	levelBarSeconds              int64 = 300
)

// LevelSweepParams are the thresholds of a sweep episode. Prices are price units; pips are converted with PipSize.
type LevelSweepParams struct {
	PipSize                          float64
	MinSweepPips, MinSweepATR        float64
	MaxSweepPips                     float64 // 0: no ceiling
	MinReclaimPips, MinReclaimATR    float64
	ReclaimMaxBars, AcceptanceCloses int
	PivotBars, LookbackBars          int
	MSSMaxBars                       int
	MinMSSBody, MinMSSDisplacement   float64
	MinMSSCloseStrength              float64
	ConfirmationMaxAge               int
	MinWickRatio                     float64
	// RequireOrigin demands the candle before the sweep closed on the origin side of the level (above it, in the bullish orientation): the
	// price came from there, so the excursion is a run through the level and not a market that already lives beyond it.
	RequireOrigin bool
}

// LevelEpisode is one confirmed sweep -> reclaim -> structure shift sequence. Every index is into the (oriented) candle series given.
type LevelEpisode struct {
	Sweep, Reclaim, Confirm, Extreme int
	// Shift is the broken swing: its price and pivot index.
	ShiftLevel float64
	ShiftPivot int
	// The shift candle's measured quality.
	BodyRatio, DisplacementATR, CloseStrength float64
	// WickRejection: a candle from the sweep to the reclaim closed up with a lower wick of at least MinWickRatio of its range.
	WickRejection bool
	// Depth is how far past the level the manipulation went (price units).
	Depth float64
}

// LevelRejection is a real excursion through the level that a rule refused.
type LevelRejection struct {
	Reason string
	Sweep  int
}

// PivotHighs returns the index of every candle whose high is strictly above the n candles on each side. A pivot at p is only knowable once
// candle p+n has closed; callers must require p+n < the candle they are deciding on.
func PivotHighs(bars []market.Candle, n int) []int {
	var out []int
	for p := n; p+n < len(bars); p++ {
		ok := true
		for k := 1; k <= n && ok; k++ {
			if bars[p-k].High >= bars[p].High || bars[p+k].High >= bars[p].High {
				ok = false
			}
		}
		if ok {
			out = append(out, p)
		}
	}
	return out
}

func barsBetween(bars []market.Candle, from, to int) int {
	return int((bars[to].Time - bars[from].Time) / levelBarSeconds)
}

// FindLevelSweeps scans the sweep candles in [from, to] (indices) for stop runs through level and follows each to its outcome. bars is the
// oriented series up to and including the evaluated candle. Only episodes whose confirming candle is at most ConfirmationMaxAge candles old are
// returned as confirmed; every refused excursion comes back with its reason. The scan resumes after each excursion, so one pass yields every
// episode of the window in time order.
func FindLevelSweeps(bars []market.Candle, atr *indicator.WilderWindow, level float64, from, to int, p LevelSweepParams) (episodes []LevelEpisode, rejections []LevelRejection) {
	asOf := len(bars) - 1
	if to > asOf {
		to = asOf
	}
	if from < 1 {
		from = 1
	}
	pivots := PivotHighs(bars, p.PivotBars)
	threshold := func(i int) (float64, bool) {
		a, ok := atr.At(i)
		if !ok {
			return 0, false
		}
		return math.Max(p.MinSweepPips*p.PipSize, p.MinSweepATR*a), true
	}
	for i := from; i <= to; {
		thr, ok := threshold(i)
		if !ok || !(bars[i].Low < level-thr) || p.RequireOrigin && !(bars[i-1].Close >= level) {
			i++
			continue
		}
		ep, kind, reason, resume := runSweep(bars, atr, pivots, level, i, p)
		switch kind {
		case episodePending:
			i = asOf + 1
		case episodeVoid:
			rejections = append(rejections, LevelRejection{Reason: reason, Sweep: i})
			i = resume
		default:
			if barsBetween(bars, ep.Confirm, asOf) <= p.ConfirmationMaxAge {
				episodes = append(episodes, ep)
			}
			i = ep.Confirm + 1
		}
	}
	return episodes, rejections
}

type episodeKind int

const (
	episodePending episodeKind = iota
	episodeVoid
	episodeConfirmed
)

func runSweep(bars []market.Candle, atr *indicator.WilderWindow, pivots []int, level float64, s0 int, p LevelSweepParams) (LevelEpisode, episodeKind, string, int) {
	asOf := len(bars) - 1
	pip := p.PipSize

	// Reclaim: a completed close back above the level (by the buffer) inside the window. A run of closes below it for AcceptanceCloses candles
	// before that is acceptance: the market is not taking the stop orders back, it is moving through them (the break setups are for that).
	r, below := -1, 0
	for j := s0; j <= asOf && barsBetween(bars, s0, j) <= p.ReclaimMaxBars; j++ {
		a, ok := atr.At(j)
		if !ok {
			return LevelEpisode{}, episodeVoid, LevelReasonATRUnavailable, j + 1
		}
		buffer := math.Max(p.MinReclaimPips*pip, p.MinReclaimATR*a)
		if bars[j].Close >= level+buffer {
			r = j
			break
		}
		if bars[j].Close < level {
			below++
		} else {
			below = 0
		}
		if p.AcceptanceCloses > 0 && below >= p.AcceptanceCloses {
			return LevelEpisode{}, episodeVoid, LevelReasonAccepted, j + 1
		}
	}
	if r < 0 {
		if barsBetween(bars, s0, asOf) < p.ReclaimMaxBars {
			return LevelEpisode{}, episodePending, "", 0
		}
		resume := asOf + 1
		for j := s0 + 1; j <= asOf; j++ {
			if bars[j].Close >= level {
				resume = j + 1
				break
			}
		}
		return LevelEpisode{}, episodeVoid, LevelReasonReclaimExpired, resume
	}

	extreme := s0
	for j := s0; j <= r; j++ {
		if bars[j].Low < bars[extreme].Low {
			extreme = j
		}
	}
	depth := level - bars[extreme].Low
	if p.MaxSweepPips > 0 && depth > p.MaxSweepPips*pip {
		return LevelEpisode{}, episodeVoid, LevelReasonSweepTooDeep, r + 1
	}
	wick := false
	for j := s0; j <= r; j++ {
		if bars[j].Close > bars[j].Open && bars[j].LowerWickRatio() >= p.MinWickRatio {
			wick = true
			break
		}
	}

	// Structure shift: a completed bullish candle strictly after the reclaim, closing above the most recent swing high that precedes the
	// manipulation extreme (inside the lookback) and was already confirmable before it, with real displacement.
	anyReference, sawBreakWithoutQuality := false, false
	for j := r + 1; j <= asOf && barsBetween(bars, r, j) <= p.MSSMaxBars; j++ {
		c := bars[j]
		if c.Close < level {
			return LevelEpisode{}, episodeVoid, LevelReasonReclaimLost, j
		}
		shift, pivot, ok := structureLevel(bars, pivots, level, extreme, j, p)
		if !ok {
			continue
		}
		anyReference = true
		if !(c.Close > shift) {
			continue
		}
		a, ok := atr.At(j)
		if !ok {
			continue
		}
		body, strength, displacement := c.BodyRatio(), c.CloseLocation(), (c.Close-c.Open)/a
		if !(c.Close > c.Open) || body < p.MinMSSBody || displacement < p.MinMSSDisplacement || strength < p.MinMSSCloseStrength {
			sawBreakWithoutQuality = true
			continue
		}
		return LevelEpisode{
			Sweep: s0, Reclaim: r, Confirm: j, Extreme: extreme, ShiftLevel: shift, ShiftPivot: pivot,
			BodyRatio: body, DisplacementATR: displacement, CloseStrength: strength, WickRejection: wick, Depth: depth,
		}, episodeConfirmed, "", 0
	}
	if barsBetween(bars, r, asOf) < p.MSSMaxBars {
		return LevelEpisode{}, episodePending, "", 0
	}
	reason := LevelReasonMSSExpired
	switch {
	case !anyReference:
		reason = LevelReasonNoStructure
	case sawBreakWithoutQuality:
		reason = LevelReasonMSSQuality
	}
	return LevelEpisode{}, episodeVoid, reason, r + 1
}

// structureLevel is the counter-trend swing the shift must break: the most recent swing high above the level that precedes the manipulation
// extreme (inside the lookback) and was already confirmable before the deciding candle j.
func structureLevel(bars []market.Candle, pivots []int, level float64, extreme, j int, p LevelSweepParams) (float64, int, bool) {
	for k := len(pivots) - 1; k >= 0; k-- {
		pv := pivots[k]
		if pv+p.PivotBars >= j || pv >= extreme {
			continue
		}
		if pv < extreme-p.LookbackBars {
			break
		}
		if bars[pv].High > level {
			return bars[pv].High, pv, true
		}
	}
	return 0, 0, false
}

// LevelRetestParams are the thresholds of a break-and-retest episode. The retest band is [level-BandBelow, level+BandAbove] in the bullish
// orientation: the break went up, price comes back down into it.
type LevelRetestParams struct {
	PipSize                         float64
	AcceptanceCloses, RetestMaxBars int
	BandBelow, BandAbove            float64 // price units
	MinWickRatio                    float64
	ConfirmationMaxAge              int
}

// LevelRetest is one confirmed break -> acceptance -> retest -> rejection sequence.
type LevelRetest struct {
	Break, AcceptEnd, Retest, Confirm int
	// RetestLow is the retest candle's low (the stop sits beyond it), RetestBandLow/High the band entered.
	RetestLow     float64
	Engulfing     bool
	WickRejection bool
}

// FindLevelRetest follows a break that closed at breakIdx: AcceptanceCloses candles must close above the level, then the FIRST candle inside the
// retest band within RetestMaxBars is the retest, and it (or the next candle) must reject: close above the band top with a lower wick of at
// least MinWickRatio, or a bullish engulfing close above the level. If the first touch does not reject, a later touch is a stale retest and
// is refused. ok is false with the
// reason when the sequence did not complete; pending (reason "") when it may still.
func FindLevelRetest(bars []market.Candle, level float64, breakIdx int, p LevelRetestParams) (r LevelRetest, reason string, ok bool) {
	asOf := len(bars) - 1
	bandLow, bandHigh := level-p.BandBelow, level+p.BandAbove

	// Acceptance.
	need := breakIdx + p.AcceptanceCloses
	for j := breakIdx + 1; j <= need; j++ {
		if j > asOf {
			return r, "", false // acceptance still forming
		}
		if !(bars[j].Close > level) {
			return r, LevelReasonBreakFailed, false
		}
	}

	// The first touch of the band after acceptance.
	touch := -1
	for j := need + 1; j <= asOf && barsBetween(bars, need, j) <= p.RetestMaxBars; j++ {
		if bars[j].Close < bandLow {
			return r, LevelReasonBreakFailed, false
		}
		if bars[j].Low <= bandHigh {
			touch = j
			break
		}
	}
	if touch < 0 {
		if barsBetween(bars, need, asOf) < p.RetestMaxBars {
			return r, "", false // still waiting for the retest
		}
		return r, LevelReasonNoRetest, false
	}

	// Rejection on the touch candle or the next.
	for j := touch; j <= touch+1; j++ {
		if j > asOf {
			return r, "", false
		}
		c := bars[j]
		wick := c.Close > bandHigh && c.Close > c.Open && c.LowerWickRatio() >= p.MinWickRatio
		engulf := j > 0 && bars[j-1].Close < bars[j-1].Open && c.Close > c.Open && c.Close >= bars[j-1].Open && c.Open <= bars[j-1].Close && c.Close > level
		if !wick && !engulf {
			continue
		}
		low := bars[touch].Low
		if bars[j].Low < low {
			low = bars[j].Low
		}
		return LevelRetest{Break: breakIdx, AcceptEnd: need, Retest: touch, Confirm: j, RetestLow: low, Engulfing: engulf && !wick, WickRejection: wick}, "", true
	}
	if touch+1 > asOf {
		return r, "", false
	}
	// The first touch did not reject. A later touch is a second retest: refused, never a second chance.
	for j := touch + 2; j <= asOf; j++ {
		if bars[j].Low <= bandHigh {
			return r, LevelReasonStaleRetest, false
		}
	}
	return r, LevelReasonRetestNotRejected, false
}
