package ifvg

import (
	"fmt"
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Detect analyses the whole series as of its last candle.
func Detect(cfg Config, bars []market.Candle) Analysis {
	if len(bars) == 0 {
		return Analysis{}
	}
	return DetectAsOf(cfg, bars, len(bars)-1)
}

// DetectAsOf analyses the series as it was known when candle e had just closed:
// it reads bars[:e+1] and nothing later, and at most the trailing requiredHistory
// candles of those, so the result is a pure function of the candles ending at e.
func DetectAsOf(cfg Config, bars []market.Candle, e int) Analysis {
	if e < 0 || e >= len(bars) {
		return Analysis{}
	}
	start := e + 1 - cfg.requiredHistory()
	if start < 0 {
		start = 0
	}
	return analyseWindow(cfg, bars[start:e+1])
}

func analyseWindow(cfg Config, window []market.Candle) Analysis {
	return resolve(detectRaw(cfg, window), nil)
}

// detectRaw is every episode of the window as of its last candle, with every
// CANDIDATE still a candidate: nothing is yet chosen between inversions made in
// the same move.
func detectRaw(cfg Config, window []market.Candle) []Episode {
	if len(window) == 0 || !validSeries(window) {
		return nil
	}
	var episodes []Episode
	for _, side := range []market.Direction{market.Buy, market.Sell} {
		oriented := window
		if side == market.Sell {
			oriented = mirror(window)
		}
		d := newDetector(cfg, oriented)
		for _, ep := range d.run() {
			episodes = append(episodes, toOriginal(ep, side))
		}
	}
	return episodes
}

// resolve keeps at most one candidate per direction: two gaps inverted by the same
// move are one trade. Candidates are ranked (freshest confirmation, then the
// stronger measured inversion, then the wider gap, then id) and the first one that
// passes accept (nil accepts all) wins; one accept refuses is recorded as
// confluence_below_floor, the others as superseded_by_fresher_inversion. Because
// the ranking is applied before the confluence floor can hide it, a weaker sibling
// that passes the floor is still published when the best fails it.
func resolve(episodes []Episode, accept func(Setup) bool) Analysis {
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
			episodes[i].Setup, episodes[i].State, episodes[i].Reason = nil, StateRejectionConfirmed, reason
		}
	}
	sort.SliceStable(episodes, func(i, j int) bool {
		if episodes[i].Gap.FormedAt != episodes[j].Gap.FormedAt {
			return episodes[i].Gap.FormedAt < episodes[j].Gap.FormedAt
		}
		if episodes[i].Direction != episodes[j].Direction {
			return episodes[i].Direction < episodes[j].Direction
		}
		if episodes[i].InvertedAt != episodes[j].InvertedAt {
			return episodes[i].InvertedAt < episodes[j].InvertedAt
		}
		return episodes[i].TouchTime < episodes[j].TouchTime
	})
	out := Analysis{Episodes: episodes}
	for _, ep := range episodes {
		if ep.Setup != nil {
			out.Setups = append(out.Setups, *ep.Setup)
		}
	}
	return out
}

func better(a, b Setup) bool {
	switch {
	case a.ConfirmedAt != b.ConfirmedAt:
		return a.ConfirmedAt > b.ConfirmedAt
	case a.Inversion.DisplacementATR != b.Inversion.DisplacementATR:
		return a.Inversion.DisplacementATR > b.Inversion.DisplacementATR
	case a.Gap.WidthATR != b.Gap.WidthATR:
		return a.Gap.WidthATR > b.Gap.WidthATR
	}
	return a.Gap.ID < b.Gap.ID
}

// toOriginal maps an episode computed in the bullish (possibly mirrored) frame back
// to real prices and the real direction. In the bullish frame the original gap is
// bearish (it resisted price) and the inversion is a close above it.
func toOriginal(ep Episode, side market.Direction) Episode {
	ep.Direction = side
	g := ep.Gap
	g.Role = "bearish"
	if side == market.Sell {
		g.Role = "bullish"
		g.Low, g.High = -ep.Gap.High, -ep.Gap.Low
	}
	g.ID = fmt.Sprintf("fvg:%s:%d", g.Role, g.FormedAt)
	ep.Gap = g
	if ep.Setup == nil {
		return ep
	}
	c := *ep.Setup
	c.Direction, c.Gap = side, g
	if side == market.Sell {
		c.EntryLow, c.EntryHigh = -ep.Setup.EntryHigh, -ep.Setup.EntryLow
		c.Stop, c.Target = -ep.Setup.Stop, -ep.Setup.Target
		c.Retest.Extreme = -ep.Setup.Retest.Extreme
	}
	ep.Setup = &c
	return ep
}

type detector struct {
	cfg  Config
	bars []market.Candle
	atr  *atrWindow
	ph   []pivot // pivot highs: the opposing structure a buy aims at
	e    int
	pip  float64
}

func newDetector(cfg Config, bars []market.Candle) *detector {
	return &detector{
		cfg: cfg, bars: bars, atr: newATRWindow(bars, cfg.ATRLength, cfg.ATRWindowBars),
		ph: pivotHighs(bars, cfg.PivotBars), e: len(bars) - 1, pip: cfg.PipSize,
	}
}

// barsBetween is the whole number of M5 periods between two candles' open times.
// Every age and window is measured in time, never in slice positions, so a gap in
// the feed ages a setup instead of hiding it.
func (d *detector) barsBetween(from, to int) int {
	return int((d.bars[to].Time - d.bars[from].Time) / m5Seconds)
}

// gap is a bearish fair-value gap in the bullish frame, in its own candles.
type gap struct {
	Gap
	formed int // index of the third candle
}

// run returns one episode per inversion that happened within the episode lookback
// (and one per distinct retest of it). Candidates are the episodes that reached
// CANDIDATE.
func (d *detector) run() []Episode {
	var out []Episode
	first := d.e
	for first > 3 && d.barsBetween(first-1, d.e) <= d.cfg.EpisodeLookback {
		first--
	}
	for j := first; j <= d.e; j++ {
		for c := j - 1; c >= 2 && d.barsBetween(c, j) <= d.cfg.MaximumInversionBars; c-- {
			g, ok := d.gapAt(c)
			if !ok || !d.firstCloseAbove(g, j) {
				continue
			}
			out = append(out, d.inversion(g, j)...)
		}
	}
	return out
}

// gapAt is the bearish fair-value gap whose third candle is c, when it qualifies:
// three consecutive closed candles, the first candle's low strictly above the
// third's high, a gap wide enough in ATR and pips, and a middle candle that is a
// real bearish displacement. Everything is judged on candles up to and including c.
func (d *detector) gapAt(c int) (gap, bool) {
	a, b, third := d.bars[c-2], d.bars[c-1], d.bars[c]
	if third.Time-b.Time != m5Seconds || b.Time-a.Time != m5Seconds {
		return gap{}, false
	}
	if !(a.Low > third.High) {
		return gap{}, false
	}
	atr, ok := d.atr.at(c)
	if !ok {
		return gap{}, false
	}
	low, high := third.High, a.Low
	width := high - low
	if width < math.Max(d.cfg.MinimumGapPips*d.pip, d.cfg.MinimumGapATR*atr) {
		return gap{}, false
	}
	// The middle candle must be the move that made the gap: a bearish displacement.
	body := b.Open - b.Close
	if !(body > 0) || body/atr < d.cfg.MinimumOriginBodyATR || bodyRatio(b) < d.cfg.MinimumOriginBody {
		return gap{}, false
	}
	return gap{
		Gap: Gap{
			FormedAt: third.Time, Low: low, High: high, WidthATR: width / atr,
			OriginBodyATR: body / atr, OriginBodyBias: bodyRatio(b),
		},
		formed: c,
	}, true
}

// firstCloseAbove reports whether j is the FIRST candle after the gap to close
// above the gap's upper bound by any amount. A wick through the gap is not a close
// through it, and an earlier close through it would have been the inversion.
func (d *detector) firstCloseAbove(g gap, j int) bool {
	if d.bars[j].Close <= g.High {
		return false
	}
	for i := g.formed + 1; i < j; i++ {
		if d.bars[i].Close > g.High {
			return false
		}
	}
	return true
}

// inversion evaluates the inverting candle j of gap g and walks the retests to the
// evaluated bar. The result is one episode per distinct touch of the inverted gap,
// or the single terminal episode that ended the inversion.
func (d *detector) inversion(g gap, j int) []Episode {
	cfg, bars, e := d.cfg, d.bars, d.e
	base := Episode{Gap: g.Gap, State: StateFVGCreated, EndedAt: bars[j].Time}
	terminal := func(ep Episode, state State, reason string, i int) []Episode {
		ep.State, ep.Reason, ep.EndedAt = state, reason, bars[i].Time
		return []Episode{ep}
	}
	atrJ, okJ := d.atr.at(j)
	// The inversion buffer is built from what was knowable BEFORE the inverting
	// candle: ATR as of the candle before it, so the candle's own range can never
	// decide whether its close was far enough.
	atrPre, okPre := d.atr.at(j - 1)
	if !okJ || !okPre {
		return nil
	}
	cj := bars[j]
	buffer := math.Max(cfg.MinimumBreakPips*d.pip, cfg.MinimumBreakATR*atrPre)
	if cj.Close <= g.High+buffer {
		return terminal(base, StateInversionFailed, ReasonInversionBuffer, j)
	}
	body := cj.Close - cj.Open
	if !(body > 0) || bodyRatio(cj) < cfg.MinInversionBody || closeStrength(cj) < cfg.MinInversionCloseStr || body/atrJ < cfg.MinInversionDisp {
		return terminal(base, StateInversionFailed, ReasonInversionQuality, j)
	}
	inv := Inversion{
		Time: cj.Time, BarsFromGap: d.barsBetween(g.formed, j), CloseBeyondATR: (cj.Close - g.High) / atrJ,
		BodyRatio: bodyRatio(cj), CloseStrength: closeStrength(cj), DisplacementATR: body / atrJ,
	}
	base.State, base.InvertedAt = StateInversionConfirmed, cj.Time

	failBuf := math.Max(cfg.RoleFlipFailPips*d.pip, cfg.RoleFlipFailATR*atrJ)
	touchTol := math.Max(cfg.RetestTouchPips*d.pip, cfg.RetestTouchATR*atrJ)
	departure := cfg.RetestDepartureATR * atrJ

	var out []Episode
	cur := base
	touch, touches, confirmed := -1, 0, 0
	armed := true // a new touch counts only after price has left the gap again
	for k := j + 1; k <= e; k++ {
		c := bars[k]
		if cur.State == StateInversionConfirmed {
			cur.State = StateRetestWaiting
		}
		if c.Close < g.Low-failBuf {
			// The close went through the whole gap: the role flip failed.
			state, reason := StateInversionFailed, ReasonRoleFlipFailed
			if touches > 0 {
				state, reason = StateRetestFailed, ReasonRetestClosedThrough
			}
			if confirmed == 0 || touch >= 0 {
				return append(out, terminalEpisode(cur, state, reason, c.Time))
			}
			return out
		}
		if touch < 0 && d.barsBetween(j, k) > cfg.RetestMaxBars {
			if confirmed > 0 {
				return out
			}
			return append(out, terminalEpisode(cur, StateExpired, ReasonRetestWindowExpired, c.Time))
		}
		if touch >= 0 && d.barsBetween(touch, k) >= cfg.ConfirmationWindow {
			out = append(out, terminalEpisode(cur, StateExpired, ReasonConfirmExpired, c.Time))
			touch = -1
			cur = base
			cur.State = StateRetestWaiting
		}
		if !armed && c.Close >= g.High+departure {
			armed = true
		}
		if touch < 0 && armed && c.Low <= g.High+touchTol {
			touch, touches = k, touches+1
			cur.TouchTime, cur.State, cur.EndedAt = c.Time, StateRetestTouched, c.Time
		}
		if touch >= 0 && d.rejects(c, g.High) {
			cur.State, cur.EndedAt = StateRejectionConfirmed, c.Time
			out = append(out, d.finish(cur, g, inv, j, touch, k, touches, atrJ, failBuf))
			confirmed++
			touch, armed = -1, false
			cur = base
			cur.State = StateRetestWaiting
			if confirmed >= cfg.MaxRetestsPerInverse {
				return out
			}
		}
	}
	if touch >= 0 || len(out) == 0 {
		out = append(out, cur)
	}
	return out
}

func terminalEpisode(ep Episode, state State, reason string, at int64) Episode {
	ep.State, ep.Reason, ep.EndedAt = state, reason, at
	return ep
}

// rejects is the directional rejection of the retested gap: a bullish candle that
// closes back above the gap's upper bound, in the upper part of its range, with
// either a lower-wick reaction or a strong body.
func (d *detector) rejects(c market.Candle, level float64) bool {
	return c.Close > c.Open && c.Close > level && closeStrength(c) >= d.cfg.RejectionCloseStr &&
		(lowerWickRatio(c) >= d.cfg.RejectionWickRatio || bodyRatio(c) >= d.cfg.RejectionBodyRatio)
}

// finish turns a confirmed rejection into a candidate, or records exactly why the
// confirmed rejection is not published.
func (d *detector) finish(ep Episode, g gap, inv Inversion, j, touch, confirm, touches int, atrJ, failBuf float64) Episode {
	cfg, bars, e := d.cfg, d.bars, d.e
	reject := func(reason string) Episode {
		ep.State, ep.Reason = StateRejectionConfirmed, reason
		return ep
	}
	atrC, ok := d.atr.at(confirm)
	if !ok {
		return reject(ReasonATRUnavailable)
	}
	retestLow := math.Inf(1)
	for i := touch; i <= confirm; i++ {
		retestLow = math.Min(retestLow, bars[i].Low)
	}
	// The stop protects the actual invalidation geometry: beyond the gap's far bound
	// and beyond whatever the retest itself traded to.
	adverse := math.Min(g.Low, retestLow)
	stop := adverse - math.Max(d.pip, cfg.InvalidationBufferATR*atrC)
	width := g.High - g.Low
	slice := width
	if cfg.EntryMaxWidthPrice > 0 && slice > cfg.EntryMaxWidthPrice {
		slice = cfg.EntryMaxWidthPrice
	}
	tolE := math.Max(cfg.EntryTolerancePips*d.pip, cfg.EntryToleranceATR*atrC)
	entryLow, entryHigh := g.High-slice, g.High+tolE
	if !(stop < entryLow) || !(entryHigh > entryLow) {
		return reject(ReasonDegenerateStop)
	}
	risk := entryHigh - stop
	riskPips := risk / d.pip

	target, found, anyAbove := d.target(j, confirm, entryHigh, atrC)
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

	for i := confirm + 1; i <= e; i++ {
		c := bars[i]
		switch {
		case c.Low <= stop || c.Close < g.Low-failBuf:
			ep.State, ep.Reason, ep.EndedAt = StateRetestFailed, ReasonInvalidatedAfter, c.Time
			return ep
		case c.High >= target:
			return reject(ReasonTargetReached)
		}
	}
	if d.barsBetween(confirm, e) > cfg.ConfirmationMaxAge {
		return reject(ReasonConfirmationStale)
	}
	last := bars[e]
	if last.Close < entryLow {
		return reject(ReasonPriceThroughEntry)
	}
	if last.Close-entryHigh > cfg.MaximumEntryDistATR*atrC {
		return reject(ReasonEntryTooFar)
	}

	deepest := g.High
	for i := j + 1; i <= confirm; i++ {
		deepest = math.Min(deepest, bars[i].Close)
	}
	integrity := 1.0
	if width > 0 {
		integrity = 1 - math.Min(width, g.High-deepest)/width
	}
	cb := bars[confirm]
	setup := Setup{
		Gap: g.Gap, Inversion: inv,
		Retest: Retest{
			TouchTime: bars[touch].Time, ConfirmedAt: cb.Time, Extreme: retestLow,
			PenetrationATR: math.Max(0, g.High-retestLow) / atrC,
			WickRatio:      lowerWickRatio(cb), CloseStrength: closeStrength(cb),
			BarsAfterInversion: d.barsBetween(j, confirm), Touches: touches,
		},
		EntryLow: entryLow, EntryHigh: entryHigh, Stop: stop, Target: target,
		ATR: atrC, RiskPips: riskPips, RewardRisk: reward / risk, TargetRoomATR: reward / atrC,
		ZoneIntegrity: integrity, WickRejection: lowerWickRatio(cb) >= cfg.RejectionWickRatio, ConfirmedAt: cb.Time,
	}
	ep.State, ep.Reason, ep.Setup, ep.EndedAt = StateCandidate, "", &setup, cb.Time
	return ep
}

// target is the nearest credible opposing structure above the entry: a confirmed
// pivot high above everything price has traded since the inversion and above the
// entry, heading a swing of at least target_swing_atr. found is false when the
// nearest credible structure leaves less than the required room; anyAbove tells
// "nothing there" from "blocked too close". The nearest decides: a closer credible
// blocker is never skipped in favour of a farther one, and no target is
// manufactured when there is none.
func (d *detector) target(j, confirm int, entryHigh, atrC float64) (price float64, found, anyAbove bool) {
	cfg := d.cfg
	floor := entryHigh
	for i := j; i <= confirm; i++ {
		floor = math.Max(floor, d.bars[i].High)
	}
	closest := math.Inf(1)
	for _, p := range d.ph {
		if d.barsBetween(p.index, confirm) > cfg.TargetLookbackBars || p.price <= floor {
			continue
		}
		low := math.Inf(1)
		for i := p.index - cfg.TargetSwingBars; i <= p.conf; i++ {
			if i >= 0 {
				low = math.Min(low, d.bars[i].Low)
			}
		}
		if p.price-low >= cfg.TargetSwingATR*atrC && p.price < closest {
			closest = p.price
		}
	}
	if math.IsInf(closest, 1) {
		return 0, false, false
	}
	if closest-entryHigh < cfg.MinimumTargetRoomATR*atrC {
		return 0, false, true
	}
	return closest, true, true
}
