package crt

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Input is the closed-candle data one evaluation reads. Both slices are
// oldest first. H1 and M5 are related only by candle time; a position in one
// slice is never used to index the other.
type Input struct {
	H1 []market.Candle
	M5 []market.Candle
}

// Detect evaluates the CRT thesis as of the last M5 candle: the most recent
// closed H1 candles anchor ranges, an M5 candle sweeps an edge, an M5 close
// reclaims it, a later M5 close shifts structure, and the entry, stop and
// target follow from those facts.
func Detect(cfg Config, in Input) Analysis {
	return detectAsOf(cfg, in, len(in.M5)-1)
}

// DetectAsOf is Detect as of M5 index asOf: candles after it (and H1 candles
// not yet closed by then) are never read. Certification uses it to prove, bar by
// bar on real captures, that a decision made with the future present equals the
// decision made with only the past.
func DetectAsOf(cfg Config, in Input, asOf int) Analysis { return detectAsOf(cfg, in, asOf) }

// detectAsOf is Detect with an explicit last usable M5 index. Candles after
// asOf are never read, which the prefix-invariance tests rely on: the result
// with the full series and asOf=k must equal the result with series[:k+1].
func detectAsOf(cfg Config, in Input, asOf int) Analysis {
	var out Analysis
	if asOf < 0 || asOf >= len(in.M5) {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInvalidCandles})
		return out
	}
	m5 := in.M5[:asOf+1]
	evalClose := m5[asOf].Time + m5Seconds
	out.EvaluatedAt = evalClose

	// Only H1 candles that have fully closed by the evaluated M5 close exist.
	h1 := in.H1
	h1 = h1[:sort.Search(len(h1), func(i int) bool { return h1[i].Time+h1Seconds > evalClose })]
	if !validSeries(h1) || !validSeries(m5) {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInvalidCandles})
		return out
	}
	h1ATR, m5ATR := newATRWindow(h1, cfg.ATRLength, cfg.ATRWindowBars), newATRWindow(m5, cfg.ATRLength, cfg.ATRWindowBars)
	if !h1ATR.enough() {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInsufficientH1History})
		return out
	}
	if !m5ATR.enough() {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInsufficientM5History})
		return out
	}

	for _, dir := range []market.Direction{market.Buy, market.Sell} {
		d := &detector{cfg: cfg, dir: dir, sign: 1, h1: h1, m5: m5, h1ATR: h1ATR, m5ATR: m5ATR, asOf: asOf, evalClose: evalClose, out: &out}
		if dir == market.Sell {
			d.sign, d.h1, d.m5 = -1, mirror(h1), mirror(m5)
		}
		d.pivots = pivotHighs(d.m5, cfg.StructurePivotBars)
		d.scan()
	}
	resolveOpposing(&out)
	sort.SliceStable(out.Setups, func(i, j int) bool {
		a, b := out.Setups[i], out.Setups[j]
		if a.ConfirmedAt != b.ConfirmedAt {
			return a.ConfirmedAt > b.ConfirmedAt
		}
		if a.Anchor.OpenTime != b.Anchor.OpenTime {
			return a.Anchor.OpenTime > b.Anchor.OpenTime
		}
		return a.Direction < b.Direction
	})
	return out
}

// detector evaluates ONE direction in its bullish orientation. For SELL the
// candles are mirrored, so every comparison below reads as the bullish case
// and the setup is reflected back on the way out.
type detector struct {
	cfg       Config
	dir       market.Direction
	sign      float64
	h1, m5    []market.Candle // oriented
	h1ATR     *atrWindow
	m5ATR     *atrWindow
	pivots    []int // oriented M5 pivot-high indices
	asOf      int
	evalClose int64
	out       *Analysis
}

func (d *detector) scan() {
	horizon := int64(d.cfg.SweepWindowH1Periods)*h1Seconds +
		int64(d.cfg.ReclaimMaxBars+d.cfg.MSSMaxBars+d.cfg.ConfirmationMaxAgeBars+1)*m5Seconds
	for a := len(d.h1) - 1; a >= 0; a-- {
		closeTime := d.h1[a].Time + h1Seconds
		if closeTime+horizon <= d.evalClose {
			break // older anchors cannot yield a fresh confirmation any more
		}
		if d.dir == market.Buy {
			d.out.Anchors++
		}
		anchor, ok := d.anchorAt(a)
		if !ok {
			continue
		}
		d.scanAnchor(anchor)
	}
}

// anchorAt builds the anchor for H1 candle a. ok is false when the candle has
// no ATR yet; a range below the configured ratio is reported (as a rejection)
// only if the market actually swept it, so quiet hours stay silent.
func (d *detector) anchorAt(a int) (Anchor, bool) {
	atr, ok := d.h1ATR.at(a)
	if !ok {
		return Anchor{}, false
	}
	c := d.h1[a]
	anchor := Anchor{OpenTime: c.Time, CloseTime: c.Time + h1Seconds, High: c.High, Low: c.Low, ATR: atr}
	width := anchor.Width()
	if width > 0 {
		anchor.RangeATR = width / atr
	}
	if width <= 0 || anchor.RangeATR < d.cfg.MinimumH1RangeATR {
		if s0 := d.firstRaid(anchor, firstAtOrAfter(d.m5, anchor.CloseTime)); s0 >= 0 {
			d.reject(ReasonH1RangeBelowMinimum, anchor, s0)
		}
		return Anchor{}, false
	}
	return anchor, true
}

func (d *detector) sweepThreshold(i int) (float64, bool) {
	atr, ok := d.m5ATR.at(i)
	if !ok {
		return 0, false
	}
	return math.Max(d.cfg.MinimumSweepPips*d.cfg.PipSize, d.cfg.MinimumSweepATR*atr), true
}

func (d *detector) reclaimBuffer(i int) (float64, bool) {
	atr, ok := d.m5ATR.at(i)
	if !ok {
		return 0, false
	}
	return math.Max(d.cfg.MinimumReclaimPips*d.cfg.PipSize, d.cfg.MinimumReclaimATR*atr), true
}

// firstRaid is the first M5 candle at or after from, inside the sweep window,
// whose low is beyond the anchor's low by the sweep threshold; -1 if none.
func (d *detector) firstRaid(anchor Anchor, from int) int {
	windowEnd := anchor.CloseTime + int64(d.cfg.SweepWindowH1Periods)*h1Seconds
	for i := from; i <= d.asOf && d.m5[i].Time < windowEnd; i++ {
		if thr, ok := d.sweepThreshold(i); ok && d.m5[i].Low < anchor.Low-thr {
			return i
		}
	}
	return -1
}

func (d *detector) reject(reason string, anchor Anchor, sweepIndex int) {
	r := Rejection{Reason: reason, Direction: d.dir, AnchorTime: anchor.OpenTime}
	if sweepIndex >= 0 && sweepIndex < len(d.m5) {
		r.SweepTime = d.m5[sweepIndex].Time
	}
	d.out.Rejections = append(d.out.Rejections, r)
}

type episodeKind int

const (
	episodePending episodeKind = iota
	episodeVoid
	episodeConfirmed
)

type episode struct {
	kind     episodeKind
	reason   string
	resumeAt int
	// Confirmed episodes.
	s0, r, m   int
	shift      *StructureShift
	level      float64
	levelIndex int
	extreme    int
}

func (d *detector) scanAnchor(anchor Anchor) {
	i0 := firstAtOrAfter(d.m5, anchor.CloseTime)
	windowEnd := anchor.CloseTime + int64(d.cfg.SweepWindowH1Periods)*h1Seconds
	var best *Setup
	for i := i0; i <= d.asOf && d.m5[i].Time < windowEnd; {
		thr, ok := d.sweepThreshold(i)
		if !ok || !(d.m5[i].Low < anchor.Low-thr) {
			i++
			continue
		}
		ep := d.runEpisode(anchor, i0, i)
		switch ep.kind {
		case episodePending:
			i = d.asOf + 1
		case episodeVoid:
			d.reject(ep.reason, anchor, i)
			i = ep.resumeAt
		case episodeConfirmed:
			if d.barsBetween(ep.m, d.asOf) <= d.cfg.ConfirmationMaxAgeBars {
				if setup, reason := d.finalize(anchor, i0, ep); reason != "" {
					d.reject(reason, anchor, ep.s0)
				} else {
					best = setup
				}
			}
			i = ep.m + 1
		}
	}
	if best != nil {
		d.out.Setups = append(d.out.Setups, d.toOriginal(*best))
	}
}

// runEpisode follows one excursion that began at M5 candle s0: reclaim, then
// structure shift. It reads candles up to asOf only.
func (d *detector) runEpisode(anchor Anchor, i0, s0 int) episode {
	cfg := d.cfg

	// Reclaim: a completed close back inside the original range, on or after
	// the sweep candle and inside the bounded window.
	r := -1
	for j := s0; j <= d.asOf && d.barsBetween(s0, j) <= cfg.ReclaimMaxBars; j++ {
		c := d.m5[j]
		if c.Close > anchor.High {
			return episode{kind: episodeVoid, reason: ReasonReclaimOvershoot, resumeAt: j + 1}
		}
		buffer, ok := d.reclaimBuffer(j)
		if !ok {
			return episode{kind: episodeVoid, reason: ReasonM5ATRUnavailable, resumeAt: j + 1}
		}
		if c.Close >= anchor.Low+buffer && c.Close <= anchor.High {
			r = j
			break
		}
	}
	if r < 0 {
		if d.barsBetween(s0, d.asOf) < cfg.ReclaimMaxBars {
			return episode{kind: episodePending}
		}
		// The excursion outlived the window: price accepted below the edge.
		// The next episode may only start after a close back at the edge.
		resume := d.asOf + 1
		for j := s0 + 1; j <= d.asOf; j++ {
			if d.m5[j].Close >= anchor.Low {
				resume = j + 1
				break
			}
		}
		return episode{kind: episodeVoid, reason: ReasonReclaimWindowExpired, resumeAt: resume}
	}

	extreme := s0
	for j := s0; j <= r; j++ {
		if d.m5[j].Low < d.m5[extreme].Low {
			extreme = j
		}
	}

	if cfg.ConfirmationMode == ConfirmationSweepReclaim {
		return episode{kind: episodeConfirmed, s0: s0, r: r, m: r, extreme: extreme}
	}

	// Structure shift: a completed bullish candle, strictly AFTER the reclaim
	// candle, closing beyond the most recent in-range swing high that was
	// confirmed before it, with real displacement. The reclaim and the shift are
	// therefore always two different candles.
	pip := cfg.PipSize
	anyReference, sawBreakWithoutQuality := false, false
	for j := r + 1; j <= d.asOf && d.barsBetween(r, j) <= cfg.MSSMaxBars; j++ {
		c := d.m5[j]
		if c.Close < anchor.Low {
			return episode{kind: episodeVoid, reason: ReasonReclaimLost, resumeAt: j}
		}
		if c.Close > anchor.High {
			return episode{kind: episodeVoid, reason: ReasonTargetAlreadyReached, resumeAt: j + 1}
		}
		level, levelIndex, ok := d.structureLevel(anchor, extreme, j)
		if !ok {
			continue
		}
		anyReference = true
		if !(c.Close > level+cfg.ConfirmationBufferPips*pip) {
			continue
		}
		atr, ok := d.m5ATR.at(j)
		if !ok {
			continue
		}
		body, strength := bodyRatio(c), closeStrength(c)
		displacement := (c.Close - c.Open) / atr
		if !(c.Close > c.Open) || body < cfg.MinimumMSSBodyRatio || displacement < cfg.MinimumMSSDisplacement || strength < cfg.MinimumMSSCloseStrength {
			sawBreakWithoutQuality = true
			continue
		}
		return episode{
			kind: episodeConfirmed, s0: s0, r: r, m: j, extreme: extreme, level: level, levelIndex: levelIndex,
			shift: &StructureShift{
				Level: level, LevelTime: d.m5[levelIndex].Time, BarTime: c.Time, Close: c.Close,
				BodyRatio: body, DisplacementATR: displacement, CloseStrength: strength, BarsAfterReclaim: d.barsBetween(r, j),
			},
		}
	}
	if d.barsBetween(r, d.asOf) < cfg.MSSMaxBars {
		return episode{kind: episodePending}
	}
	reason := ReasonMSSWindowExpired
	switch {
	case !anyReference:
		reason = ReasonNoStructureReference
	case sawBreakWithoutQuality:
		reason = ReasonMSSQualityInsufficient
	}
	return episode{kind: episodeVoid, reason: reason, resumeAt: r + 1}
}

// structureLevel is the counter-trend swing the shift must break: the most
// recent swing high that precedes the manipulation extreme (within the
// lookback), lies inside the anchor range, and was already CONFIRMABLE before
// the deciding candle j (its n right-hand candles all closed before j).
func (d *detector) structureLevel(anchor Anchor, extreme, j int) (float64, int, bool) {
	n := d.cfg.StructurePivotBars
	for k := len(d.pivots) - 1; k >= 0; k-- {
		p := d.pivots[k]
		if p+n >= j || p >= extreme {
			continue
		}
		if p < extreme-d.cfg.StructureLookbackBars {
			break
		}
		if level := d.m5[p].High; level > anchor.Low && level < anchor.High {
			return level, p, true
		}
	}
	return 0, 0, false
}

// finalize applies the double-raid rule and builds the entry, stop and target
// for a confirmed, fresh episode. A non-empty reason means the episode is a
// real setup that a technical or execution-envelope rule refused.
func (d *detector) finalize(anchor Anchor, i0 int, ep episode) (*Setup, string) {
	cfg := d.cfg
	m, s0 := ep.m, ep.s0
	atr, ok := d.m5ATR.at(m)
	if !ok {
		return nil, ReasonM5ATRUnavailable
	}

	// Both extremes raided: ambiguous unless the opposite raid came strictly
	// earlier and was closed back inside before this sweep began, which makes
	// this the later, directionally coherent raid.
	lastOppositeBefore, oppositeDuring := -1, false
	for k := i0; k <= m; k++ {
		thr, ok := d.sweepThreshold(k)
		if ok && d.m5[k].High > anchor.High+thr {
			if k < s0 {
				lastOppositeBefore = k
			} else {
				oppositeDuring = true
			}
		}
	}
	resolved := false
	if oppositeDuring {
		return nil, ReasonBothExtremesRaided
	}
	if lastOppositeBefore >= 0 {
		closedBack := false
		for k := lastOppositeBefore; k < s0; k++ {
			if d.m5[k].Close <= anchor.High {
				closedBack = true
				break
			}
		}
		if !closedBack {
			return nil, ReasonBothExtremesRaided
		}
		resolved = true
	}

	// The objective must not already have been touched on the way here.
	for k := s0; k <= d.asOf; k++ {
		if d.m5[k].High >= anchor.High {
			return nil, ReasonTargetAlreadyReached
		}
	}
	// Manipulation extreme: the deepest low since the anchor closed through
	// the confirmation (earlier excursions that were voided included: price has
	// already shown it can travel there). The stop must sit beyond it.
	extremeIdx := i0
	for k := i0; k <= m; k++ {
		if d.m5[k].Low < d.m5[extremeIdx].Low {
			extremeIdx = k
		}
	}
	extreme := d.m5[extremeIdx].Low

	// Nothing since the confirmation may have invalidated the thesis: a close
	// back below the swept edge, or ANY trade (a wick) through the manipulation
	// extreme, which is where the stop sits.
	for k := m + 1; k <= d.asOf; k++ {
		if d.m5[k].Close < anchor.Low || d.m5[k].Low < extreme {
			return nil, ReasonInvalidatedAfterConfirm
		}
	}
	pip := cfg.PipSize

	depth := math.Max(pip, cfg.EntryDepthATR*atr)
	tolerance := math.Max(pip, cfg.EntryToleranceATR*atr)
	var lower, upper float64
	switch cfg.EntryModel {
	case EntryMSSRetest:
		lower, upper = math.Max(ep.level-depth, anchor.Low), ep.level+tolerance
	default: // EntryReclaimRetest: a band centred on the reclaimed edge
		lower, upper = anchor.Low-tolerance, anchor.Low+tolerance
	}
	if upper-lower > cfg.EntryMaxWidthPrice {
		lower = upper - cfg.EntryMaxWidthPrice
	}
	if !(upper > lower) {
		return nil, ReasonDegenerateEntryBand
	}
	if last := d.m5[d.asOf].Close; last < lower || last >= anchor.High {
		if last >= anchor.High {
			return nil, ReasonTargetAlreadyReached
		}
		return nil, ReasonPriceThroughEntry
	}

	stop := extreme - math.Max(pip, cfg.InvalidationBufferATR*atr)
	if !(stop < extreme) || !(stop < lower) {
		return nil, ReasonInvalidStopGeometry
	}
	target := anchor.High
	reward, risk := target-upper, upper-stop
	if !(reward > 0) || reward < cfg.MinimumTargetRoomATR*atr {
		return nil, ReasonInsufficientTargetRoom
	}
	riskPips, rewardPips := risk/pip, reward/pip
	if cfg.ExecutionStopMaxPips > 0 && riskPips > cfg.ExecutionStopMaxPips {
		return nil, ReasonRiskExceedsEnvelope
	}
	rr := reward / risk
	if rr < cfg.MinimumRewardRisk {
		return nil, ReasonRewardRiskBelowMinimum
	}

	wick := false
	for k := s0; k <= ep.r; k++ {
		if bullishWickRejection(d.m5[k]) {
			wick = true
			break
		}
	}
	reclaimBar := d.m5[ep.r]
	setup := &Setup{
		Direction: d.dir, Anchor: anchor,
		Sweep: Sweep{
			BarTime: d.m5[s0].Time, ExtremePrice: extreme, ExtremeTime: d.m5[extremeIdx].Time,
			Depth: anchor.Low - extreme, DepthATR: (anchor.Low - extreme) / atr,
		},
		Reclaim: Reclaim{
			BarTime: reclaimBar.Time, Close: reclaimBar.Close, Depth: reclaimBar.Close - anchor.Low,
			DepthATR: (reclaimBar.Close - anchor.Low) / atr, BarsAfterSweep: ep.r - s0,
		},
		Shift: ep.shift, ConfirmedAt: d.m5[m].Time, ConfirmationAge: d.barsBetween(m, d.asOf), DoubleRaidResolved: resolved,
		EntryLow: lower, EntryHigh: upper, Stop: stop, Target: target, M5ATR: atr,
		RiskPips: riskPips, RewardPips: rewardPips, TechnicalRR: rr, WickRejection: wick,
	}
	if thr, ok := d.sweepThreshold(s0); ok {
		setup.Sweep.Threshold = thr
	}
	if atrSweep, ok := d.m5ATR.at(s0); ok {
		setup.Sweep.DepthATR = (anchor.Low - extreme) / atrSweep
		setup.Reclaim.DepthATR = (reclaimBar.Close - anchor.Low) / atrSweep
	}
	return setup, ""
}

// toOriginal reflects a setup built in the bullish orientation back to the
// direction that was asked for. Magnitudes (depths, ATR multiples, pips) are
// reflection-invariant; absolute prices are not.
func (d *detector) toOriginal(s Setup) Setup {
	if d.sign > 0 {
		return s
	}
	s.Anchor.High, s.Anchor.Low = -s.Anchor.Low, -s.Anchor.High
	s.Sweep.ExtremePrice = -s.Sweep.ExtremePrice
	s.Reclaim.Close = -s.Reclaim.Close
	if s.Shift != nil {
		shift := *s.Shift
		shift.Level, shift.Close = -shift.Level, -shift.Close
		s.Shift = &shift
	}
	s.EntryLow, s.EntryHigh = -s.EntryHigh, -s.EntryLow
	s.Stop, s.Target = -s.Stop, -s.Target
	return s
}

// resolveOpposing keeps one direction per anchor when both produced a fresh
// setup: the later confirmation is the coherent one. Equal confirmation times
// are ambiguous and neither is kept.
func resolveOpposing(a *Analysis) {
	keep := a.Setups[:0:0]
	for i, s := range a.Setups {
		superseded, tied := false, false
		for j, other := range a.Setups {
			if i == j || other.Anchor.OpenTime != s.Anchor.OpenTime || other.Direction == s.Direction {
				continue
			}
			switch {
			case other.ConfirmedAt > s.ConfirmedAt:
				superseded = true
			case other.ConfirmedAt == s.ConfirmedAt:
				tied = true
			}
		}
		switch {
		case tied:
			a.Rejections = append(a.Rejections, Rejection{Reason: ReasonBothExtremesRaided, Direction: s.Direction, AnchorTime: s.Anchor.OpenTime, SweepTime: s.Sweep.BarTime})
		case superseded:
			a.Rejections = append(a.Rejections, Rejection{Reason: ReasonSupersededByOpposite, Direction: s.Direction, AnchorTime: s.Anchor.OpenTime, SweepTime: s.Sweep.BarTime})
		default:
			keep = append(keep, s)
		}
	}
	a.Setups = keep
}

// barsBetween is the whole number of M5 periods between two candles' open
// times. Every age and window in this file is measured in time, never in slice
// positions, so a gap in the feed ages a confirmation instead of hiding it.
func (d *detector) barsBetween(from, to int) int {
	return int((d.m5[to].Time - d.m5[from].Time) / m5Seconds)
}
