package sessionlevel

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Input is the closed candles one evaluation may read: M5 for the reaction and the
// session levels, H1 for the previous day and week.
type Input struct {
	M5, H1 []market.Candle
}

// Detect analyses the input as of its last M5 candle.
func Detect(cfg Config, in Input) Analysis {
	if len(in.M5) == 0 {
		return Analysis{}
	}
	return DetectAsOf(cfg, in, len(in.M5)-1)
}

// DetectAsOf analyses the input as it was known when M5 candle e had just closed:
// it reads M5[:e+1] (the trailing requiredHistory candles of those) and the H1
// candles already closed at that moment (the trailing requiredHigherHistory hours
// of those), nothing later.
func DetectAsOf(cfg Config, in Input, e int) Analysis {
	if e < 0 || e >= len(in.M5) {
		return Analysis{}
	}
	window, h1, now := prepare(cfg, in, e)
	return resolve(detectRaw(cfg, window, h1, now), nil)
}

// prepare slices the input to exactly what a decision at M5 index e may depend on.
func prepare(cfg Config, in Input, e int) (window, h1 []market.Candle, now int64) {
	start := e + 1 - cfg.requiredHistory()
	if start < 0 {
		start = 0
	}
	window = in.M5[start : e+1]
	now = in.M5[e].Time + m5Seconds
	oldest := now - int64(cfg.requiredHigherHistory())*h1Seconds
	for _, c := range in.H1 {
		if c.Time >= oldest && c.Time+h1Seconds <= now {
			h1 = append(h1, c)
		}
	}
	return window, h1, now
}

type raw struct {
	levels   []Level
	rejected []Rejected
	episodes []Episode
}

// detectRaw builds the levels and every episode of the window as of its last
// candle, with every CANDIDATE still a candidate: nothing is yet chosen between
// simultaneous reactions.
func detectRaw(cfg Config, window, h1 []market.Candle, now int64) raw {
	if len(window) == 0 || !validSeries(window) {
		return raw{}
	}
	if !validSeries(h1) {
		h1 = nil
	}
	levels, rejected := buildLevels(cfg, window, h1, now)
	for i := range levels {
		levels[i].ReachedAt = reachedAt(levels[i], window, h1)
	}
	out := raw{levels: levels, rejected: rejected}
	for _, side := range []market.Direction{market.Buy, market.Sell} {
		oriented := window
		if side == market.Sell {
			oriented = mirror(window)
		}
		d := newDetector(cfg, oriented, levels, side)
		for _, ep := range d.run() {
			out.episodes = append(out.episodes, toOriginal(ep, side))
		}
	}
	return out
}

// reachedAt is the open time of the first candle after the level became available
// that traded to the level's price (a high at or above a resistance, a low at or
// below a support), or 0 when none did. Hourly candles cover the part of the
// period before the M5 window begins.
func reachedAt(l Level, m5, h1 []market.Candle) int64 {
	hit := func(c market.Candle) bool {
		if l.Side == SideResistance {
			return c.High >= l.Price
		}
		return c.Low <= l.Price
	}
	first := int64(math.MaxInt64)
	if len(m5) > 0 {
		first = m5[0].Time
	}
	for _, c := range h1 {
		if c.Time >= l.AvailableAt && c.Time+h1Seconds <= first && hit(c) {
			return c.Time
		}
	}
	for _, c := range m5 {
		if c.Time >= l.AvailableAt && hit(c) {
			return c.Time
		}
	}
	return 0
}

// resolve keeps at most one candidate per direction: two levels reacting together
// are one trade. Candidates are ranked by evidence (freshest confirmation, the room
// to the target in R, the strength of the rejection, the significance of the level,
// then id) and the first one that passes accept (nil accepts all) wins; one accept
// refuses is recorded as confluence_below_floor, the others as superseded.
func resolve(r raw, accept func(Setup) bool) Analysis {
	episodes := r.episodes
	byDirection := map[market.Direction][]int{}
	for i, ep := range episodes {
		if ep.Setup != nil {
			byDirection[ep.Direction] = append(byDirection[ep.Direction], i)
		}
	}
	for _, idx := range byDirection {
		sort.SliceStable(idx, func(x, y int) bool { return better(*episodes[idx[x]].Setup, *episodes[idx[y]].Setup) })
		chosen := false
		for _, i := range idx {
			ok := accept == nil || accept(*episodes[i].Setup)
			if ok && !chosen {
				chosen = true
				continue
			}
			reason := ReasonSuperseded
			if !ok {
				reason = ReasonConfluenceFloor
			}
			episodes[i].Setup, episodes[i].State, episodes[i].Reason = nil, StateReactionConfirm, reason
		}
	}
	sort.SliceStable(episodes, func(i, j int) bool {
		a, b := episodes[i], episodes[j]
		switch {
		case a.Level.ID != b.Level.ID:
			return a.Level.ID < b.Level.ID
		case a.Direction != b.Direction:
			return a.Direction < b.Direction
		case a.EpisodeAt != b.EpisodeAt:
			return a.EpisodeAt < b.EpisodeAt
		}
		return a.Model < b.Model
	})
	out := Analysis{Levels: r.levels, Rejected: r.rejected, Episodes: episodes}
	for _, ep := range episodes {
		if ep.Setup != nil {
			out.Setups = append(out.Setups, *ep.Setup)
		}
	}
	return out
}

func kindRank(kind string) int {
	switch kind {
	case KindWeek:
		return 3
	case KindDay:
		return 2
	}
	return 1
}

func better(a, b Setup) bool {
	switch {
	case a.ConfirmedAt != b.ConfirmedAt:
		return a.ConfirmedAt > b.ConfirmedAt
	case a.RewardRisk != b.RewardRisk:
		return a.RewardRisk > b.RewardRisk
	case a.Reaction.RejectionStrength != b.Reaction.RejectionStrength:
		return a.Reaction.RejectionStrength > b.Reaction.RejectionStrength
	case kindRank(a.Level.Kind) != kindRank(b.Level.Kind):
		return kindRank(a.Level.Kind) > kindRank(b.Level.Kind)
	}
	return a.Level.ID < b.Level.ID
}

// toOriginal maps an episode computed in the bullish (possibly mirrored) frame back
// to real prices and the real direction. The level itself is always the real one.
func toOriginal(ep Episode, side market.Direction) Episode {
	ep.Direction = side
	if ep.Setup == nil {
		return ep
	}
	c := *ep.Setup
	c.Direction = side
	if side == market.Sell {
		c.EntryLow, c.EntryHigh = -ep.Setup.EntryHigh, -ep.Setup.EntryLow
		c.Stop, c.Target = -ep.Setup.Stop, -ep.Setup.Target
		c.Reaction.Extreme = -ep.Setup.Reaction.Extreme
	}
	ep.Setup = &c
	return ep
}

// frameLevel is a level seen from the bullish frame: a support at its own price, or
// (for the mirror) a resistance at its negated price.
type frameLevel struct {
	Level
	price float64
	first int  // index of the first candle opening at or after the level's availability; -1 when none
	known bool // the window reaches back to the level's availability
}

type frameTarget struct {
	price     float64
	reachedAt int64
	id        string
}

type detector struct {
	cfg     Config
	bars    []market.Candle
	atr     *atrWindow
	ph      []pivot
	e       int
	pip     float64
	levels  []frameLevel
	targets []frameTarget
}

func newDetector(cfg Config, bars []market.Candle, levels []Level, side market.Direction) *detector {
	d := &detector{
		cfg: cfg, bars: bars, atr: newATRWindow(bars, cfg.ATRLength, cfg.ATRWindowBars),
		ph: pivotHighs(bars, cfg.PivotBars), e: len(bars) - 1, pip: cfg.PipSize,
	}
	sign, own, opposing := 1.0, SideSupport, SideResistance
	if side == market.Sell {
		sign, own, opposing = -1.0, SideResistance, SideSupport
	}
	for _, l := range levels {
		switch l.Side {
		case own:
			fl := frameLevel{Level: l, price: sign * l.Price, first: -1, known: len(bars) > 0 && bars[0].Time <= l.AvailableAt}
			for i, c := range bars {
				if c.Time >= l.AvailableAt {
					fl.first = i
					break
				}
			}
			d.levels = append(d.levels, fl)
		case opposing:
			d.targets = append(d.targets, frameTarget{price: sign * l.Price, reachedAt: l.ReachedAt, id: l.ID})
		}
	}
	return d
}

// barsBetween is the whole number of M5 periods between two candles' open times.
// Every age and window is measured in time, never in slice positions, so a gap in
// the feed ages a setup instead of hiding it.
func (d *detector) barsBetween(from, to int) int {
	return int((d.bars[to].Time - d.bars[from].Time) / m5Seconds)
}

// atrPre is the ATR as of the candle BEFORE k: every threshold a candle is judged
// against is built from what was knowable before it, so its own range can never
// decide whether it was far enough.
func (d *detector) atrPre(k int) (float64, bool) { return d.atr.at(k - 1) }

func (d *detector) buf(pips, atrMult, atr float64) float64 {
	return math.Max(pips*d.pip, atrMult*atr)
}

// touch is the start of one fresh interaction with a level.
type touch struct {
	idx         int
	index       int  // how many touches the level had before this one
	sweptBefore bool // the level had already been swept since it became available
}

// touches lists the distinct interactions with the level since it became
// available. An interaction is a candle trading to within the touch tolerance of
// the level; a new one counts only after price has closed away from the level
// again and the separation has passed, so one dwell at the level is one touch.
func (d *detector) touches(l frameLevel) []touch {
	var out []touch
	armed, swept, last := true, false, -1
	for k := l.first; k >= 0 && k <= d.e; k++ {
		atr, ok := d.atrPre(k)
		if !ok {
			continue
		}
		c := d.bars[k]
		interacts := c.Low <= l.price+d.buf(d.cfg.TouchTolerancePips, d.cfg.TouchToleranceATR, atr)
		if armed && interacts && (last < 0 || d.barsBetween(last, k) >= d.cfg.TouchSeparationBars) {
			out = append(out, touch{idx: k, index: len(out), sweptBefore: swept})
			armed, last = false, k
		} else if !armed && c.Close >= l.price+d.cfg.ApproachMinATR*atr {
			armed = true
		}
		if c.Low < l.price-d.buf(d.cfg.SweepThresholdPips, d.cfg.SweepThresholdATR, atr) {
			swept = true
		}
	}
	return out
}

// run returns one episode per touch of every level within the episode lookback
// (more than one when a touch is first a rejection attempt, then a sweep).
func (d *detector) run() []Episode {
	var out []Episode
	for _, l := range d.levels {
		starts := d.touches(l)
		for i, st := range starts {
			if d.barsBetween(st.idx, d.e) > d.cfg.EpisodeLookback {
				continue
			}
			end := d.e
			if i+1 < len(starts) {
				end = starts[i+1].idx - 1
			}
			out = append(out, d.walk(l, st, end)...)
		}
	}
	return out
}

func terminalEpisode(ep Episode, state State, reason string, at int64) Episode {
	ep.State, ep.Reason, ep.EndedAt = state, reason, at
	return ep
}

// rejects is the directional reaction of a candle off the level: a bullish candle
// that closes at or above the threshold, in the upper part of its range, with
// either a lower-wick reaction or a strong body.
func (d *detector) rejects(c market.Candle, threshold float64) bool {
	return c.Close > c.Open && c.Close >= threshold && closeStrength(c) >= d.cfg.RejectionCloseStrength &&
		(lowerWickRatio(c) >= d.cfg.RejectionWickRatio || bodyRatio(c) >= d.cfg.RejectionBodyRatio)
}

type mode int

const (
	modeFirstTouch mode = iota
	modeWatch
	modeSweep
)

// walk follows one touch of a level forward. The touch is first judged as a
// first-touch rejection attempt; a candle that trades beyond the sweep threshold
// before the attempt confirms escalates it to a sweep, which must then be reclaimed
// within a bounded window. A sweep is never "any reclaim": the reclaim candle must
// itself be the rejection, after the sweep.
func (d *detector) walk(l frameLevel, st touch, end int) []Episode {
	cfg, bars := d.cfg, d.bars
	p, t := l.price, st.idx
	base := Episode{Level: l.Level, Model: ModelFirstTouch, State: StateTouched, EpisodeAt: bars[t].Time, EndedAt: bars[t].Time}

	atrT, ok := d.atrPre(t)
	if !ok {
		return nil
	}
	if !d.approached(l, t, atrT) {
		return []Episode{terminalEpisode(base, StateExpired, ReasonNoApproach, bars[t].Time)}
	}

	var out []Episode
	m, cur := modeFirstTouch, base
	switch {
	case !l.known:
		out = append(out, terminalEpisode(base, StateExpired, ReasonHistoryIndeterminate, bars[t].Time))
		m = modeWatch
	case st.sweptBefore:
		out = append(out, terminalEpisode(base, StateExpired, ReasonLevelSwept, bars[t].Time))
		m = modeWatch
	case st.index > cfg.MaximumPriorTouches:
		out = append(out, terminalEpisode(base, StateExpired, ReasonPriorTouches, bars[t].Time))
		m = modeWatch
	}

	sweep, below := -1, 0
	for k := t; k <= end; k++ {
		c := bars[k]
		atr, ok := d.atrPre(k)
		if !ok {
			continue
		}
		if m != modeSweep && c.Low < p-d.buf(cfg.SweepThresholdPips, cfg.SweepThresholdATR, atr) {
			if m == modeFirstTouch && k > t {
				out = append(out, terminalEpisode(cur, StateEscalatedToSweep, "", c.Time))
			}
			m, sweep = modeSweep, k
			cur = base
			cur.Model, cur.State, cur.EpisodeAt, cur.EndedAt = ModelSweepReclaim, StateSwept, c.Time, c.Time
			if l.known && st.index >= cfg.MaxEpisodesPerLevel {
				return append(out, terminalEpisode(cur, StateExpired, ReasonEpisodeLimit, c.Time))
			}
		}
		switch m {
		case modeFirstTouch:
			switch {
			case c.Close < p-d.buf(cfg.FailBufferPips, cfg.FailBufferATR, atr):
				out = append(out, terminalEpisode(cur, StateReactionFailed, ReasonClosedThrough, c.Time))
				m = modeWatch
			case d.barsBetween(t, k) >= cfg.ConfirmationWindow:
				out = append(out, terminalEpisode(cur, StateExpired, ReasonConfirmExpired, c.Time))
				m = modeWatch
			case d.rejects(c, p+d.buf(cfg.HoldBufferPips, cfg.HoldBufferATR, atr)):
				cur.State, cur.EndedAt = StateReactionConfirm, c.Time
				out = append(out, d.finish(cur, l, st, t, k))
				m = modeWatch
			}
		case modeSweep:
			if d.barsBetween(sweep, k) > cfg.ReclaimMaxBars {
				return append(out, terminalEpisode(cur, StateExpired, ReasonReclaimExpired, c.Time))
			}
			if c.Close < p-d.buf(cfg.BreakoutBufferPips, cfg.BreakoutBufferATR, atr) {
				below++
			} else {
				below = 0
			}
			switch {
			case below >= cfg.BreakoutAccept:
				return append(out, terminalEpisode(cur, StateBreakoutAccepted, ReasonBreakoutAccepted, c.Time))
			case d.rejects(c, p+d.buf(cfg.ReclaimBufferPips, cfg.ReclaimBufferATR, atr)):
				cur.State, cur.EndedAt = StateReactionConfirm, c.Time
				return append(out, d.finish(cur, l, st, sweep, k))
			case c.Close >= p:
				cur.State, cur.EndedAt = StateReclaimWaiting, c.Time
			}
		}
	}
	// An attempt still undecided at the end of its span either ran into the next
	// touch (it expired there) or is simply still open at the evaluated candle.
	if m != modeWatch {
		if end < d.e {
			reason := ReasonConfirmExpired
			if m == modeSweep {
				reason = ReasonReclaimExpired
			}
			cur = terminalEpisode(cur, StateExpired, reason, bars[end+1].Time)
		}
		out = append(out, cur)
	}
	return out
}

// approached is true when price was trading clearly above the level shortly before
// the touch, so the touch is a return to the level and not a dwell on it.
func (d *detector) approached(l frameLevel, t int, atr float64) bool {
	for i := t - 1; i >= 0 && d.barsBetween(i, t) <= d.cfg.ApproachLookback; i-- {
		if d.bars[i].Close >= l.price+d.cfg.ApproachMinATR*atr {
			return true
		}
	}
	return false
}

// finish turns a confirmed reaction into a candidate, or records exactly why the
// confirmed reaction is not published. st is the touch, ev the touch (first-touch)
// or the sweep (sweep/reclaim) and c the confirming candle.
func (d *detector) finish(ep Episode, l frameLevel, st touch, ev, c int) Episode {
	cfg, bars, e := d.cfg, d.bars, d.e
	p, t := l.price, st.idx
	reject := func(reason string) Episode {
		ep.State, ep.Reason = StateReactionConfirm, reason
		return ep
	}
	atrC, ok := d.atr.at(c)
	if !ok {
		return reject(ReasonATRUnavailable)
	}

	// The adverse extreme includes the sweep wick: the stop is beyond the level AND
	// beyond everything price traded to in the reaction, never inside it.
	extreme := math.Inf(1)
	for i := t; i <= c; i++ {
		extreme = math.Min(extreme, bars[i].Low)
	}
	stop := math.Min(p, extreme) - math.Max(d.pip, cfg.InvalidationBufferATR*atrC)
	tolE := d.buf(cfg.EntryTolerancePips, cfg.EntryToleranceATR, atrC)
	entryLow, entryHigh := p, p+math.Max(cfg.EntryBandATR*atrC, tolE)
	if !(stop < entryLow) || !(entryHigh > entryLow) {
		return reject(ReasonDegenerateStop)
	}
	risk := entryHigh - stop
	riskPips := risk / d.pip

	target, kind, found, anyAbove := d.target(t, c, entryHigh, atrC)
	switch {
	case !found && !anyAbove:
		return reject(ReasonNoOpposingStructure)
	case !found:
		return reject(ReasonInsufficientRoom)
	}
	if cfg.ExecutionStopMaxPips > 0 && riskPips > cfg.ExecutionStopMaxPips+1e-9 {
		return reject(ReasonRiskEnvelope)
	}
	reward := target - entryHigh
	if reward/risk < cfg.MinimumRewardRisk-1e-12 {
		return reject(ReasonRewardRisk)
	}

	failBuf := d.buf(cfg.FailBufferPips, cfg.FailBufferATR, atrC)
	for i := c + 1; i <= e; i++ {
		b := bars[i]
		switch {
		case b.Low <= stop || b.Close < p-failBuf:
			ep.State, ep.Reason, ep.EndedAt = StateReactionFailed, ReasonInvalidatedAfter, b.Time
			return ep
		case b.High >= target:
			return reject(ReasonTargetReached)
		}
	}
	if d.barsBetween(c, e) > cfg.ConfirmationMaxAge {
		return reject(ReasonConfirmationStale)
	}
	last := bars[e]
	if last.Close < entryLow-tolE {
		return reject(ReasonPriceThroughEntry)
	}
	if last.Close-entryHigh > cfg.MaximumEntryDistATR*atrC {
		return reject(ReasonEntryTooFar)
	}

	below := 0
	for i := l.first; i <= c; i++ {
		if bars[i].Close < p {
			below++
		}
	}
	span := c - l.first + 1
	integrity := 1.0
	if span > 0 {
		integrity = 1 - float64(below)/float64(span)
	}
	cb := bars[c]
	re := Reaction{
		Model: ep.Model, EpisodeAt: bars[ev].Time, ConfirmedAt: cb.Time, Extreme: extreme,
		ReclaimDepthATR:   math.Max(0, cb.Close-p) / atrC,
		RejectionStrength: closeStrength(cb), WickRatio: lowerWickRatio(cb), BodyRatio: bodyRatio(cb),
		BarsToConfirm: d.barsBetween(ev, c),
	}
	if ep.Model == ModelSweepReclaim {
		sweepLow := math.Inf(1)
		for i := ev; i <= c; i++ {
			sweepLow = math.Min(sweepLow, bars[i].Low)
		}
		re.SweepDepthATR = math.Max(0, p-sweepLow) / atrC
	} else {
		re.PenetrationATR = math.Max(0, p-extreme) / atrC
	}
	setup := Setup{
		Level: l.Level, Reaction: re,
		EntryLow: entryLow, EntryHigh: entryHigh, Stop: stop, Target: target, TargetKind: kind,
		ATR: atrC, RiskPips: riskPips, RewardRisk: reward / risk, TargetRoomATR: reward / atrC,
		PriorTouches: st.index, TouchesKnown: l.known,
		LevelAgeBars:  int((cb.Time - l.AvailableAt) / m5Seconds),
		WickRejection: lowerWickRatio(cb) >= cfg.RejectionWickRatio, DisplacementATR: (cb.Close - cb.Open) / atrC,
		LevelIntegrity: integrity, ConfirmedAt: cb.Time,
	}
	ep.State, ep.Reason, ep.Setup, ep.EndedAt = StateCandidate, "", &setup, cb.Time
	return ep
}

// target is the nearest credible opposing liquidity above the entry: a confirmed
// pivot high above everything price has traded since the touch heading a swing of
// at least target_swing_atr, or an opposing reference level price has not yet
// traded to. found is false when the nearest credible liquidity leaves less than the
// required room; anyAbove tells "nothing there" from "blocked too close". The
// nearest decides: no target is manufactured when there is none.
func (d *detector) target(t, c int, entryHigh, atrC float64) (price float64, kind string, found, anyAbove bool) {
	cfg := d.cfg
	floor := entryHigh
	for i := t; i <= c; i++ {
		floor = math.Max(floor, d.bars[i].High)
	}
	closest, closestKind := math.Inf(1), ""
	for _, p := range d.ph {
		// A pivot counts only when it was confirmed by the time the reaction was: a
		// later one would move the target of a setup that was already published.
		if p.conf > c || d.barsBetween(p.index, c) > cfg.TargetLookbackBars || p.price <= floor {
			continue
		}
		low := math.Inf(1)
		for i := p.index - cfg.TargetSwingBars; i <= p.conf; i++ {
			if i >= 0 {
				low = math.Min(low, d.bars[i].Low)
			}
		}
		if p.price-low >= cfg.TargetSwingATR*atrC && p.price < closest {
			closest, closestKind = p.price, "swing"
		}
	}
	for _, o := range d.targets {
		if o.price <= floor || (o.reachedAt != 0 && o.reachedAt <= d.bars[c].Time) {
			continue
		}
		if o.price < closest {
			closest, closestKind = o.price, "level"
		}
	}
	if math.IsInf(closest, 1) {
		return 0, "", false, false
	}
	if closest-entryHigh < cfg.MinimumTargetRoomATR*atrC {
		return 0, "", false, true
	}
	return closest, closestKind, true, true
}
