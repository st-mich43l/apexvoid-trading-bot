package breakretest

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

// DetectAsOf analyses the series as it was known when candle e had just
// closed: it reads bars[:e+1] and nothing later, and at most the trailing
// requiredHistory candles of those, so the result is a pure function of the
// candles ending at e. Tests use it to prove prefix invariance.
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

// analyseWindow is the decision as of the LAST candle of window, reading only
// window. DetectAsOf hands it exactly the trailing requiredHistory candles;
// tests hand it more to prove that is enough.
func analyseWindow(cfg Config, window []market.Candle) Analysis {
	return resolve(detectRaw(cfg, window), nil)
}

// detectRaw is every episode of the window as of its last candle, with every
// CANDIDATE still a candidate: nothing is yet chosen between references broken in
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

// resolve keeps at most one candidate per direction: two references broken in the
// same move are one trade. Candidates are ranked (freshest confirmation, then
// stronger measured break, then more touches, then reference id) and the first one
// that passes accept (nil accepts all) wins; a candidate accept refuses is recorded
// as confluence_below_floor, the others as superseded_by_stronger_reference. Because
// the ranking is applied BEFORE the confluence floor would otherwise hide it, a
// weaker sibling that passes the floor is still published when the best fails it.
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
			episodes[i].Setup, episodes[i].State, episodes[i].Reason = nil, StateRetestConfirmed, reason
		}
	}
	sort.SliceStable(episodes, func(i, j int) bool {
		if episodes[i].BreakStart != episodes[j].BreakStart {
			return episodes[i].BreakStart < episodes[j].BreakStart
		}
		if episodes[i].Direction != episodes[j].Direction {
			return episodes[i].Direction < episodes[j].Direction
		}
		return episodes[i].Reference.ID < episodes[j].Reference.ID
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
	case a.Break.DisplacementATR != b.Break.DisplacementATR:
		return a.Break.DisplacementATR > b.Break.DisplacementATR
	case a.Reference.Touches != b.Reference.Touches:
		return a.Reference.Touches > b.Reference.Touches
	}
	return a.Reference.ID < b.Reference.ID
}

// toOriginal maps an episode computed in the bullish (possibly mirrored) frame
// back to real prices and the real direction.
func toOriginal(ep Episode, side market.Direction) Episode {
	ep.Direction = side
	ep.Reference.SideBefore = "below"
	if side == market.Sell {
		ep.Reference.SideBefore = "above"
		ep.Reference.PriceAtBreak = -ep.Reference.PriceAtBreak
		ep.Reference.Slope = -ep.Reference.Slope
	}
	if ep.Setup == nil {
		return ep
	}
	c := *ep.Setup
	c.Direction, c.Reference = side, ep.Reference
	if side == market.Sell {
		c.EntryLow, c.EntryHigh = -ep.Setup.EntryHigh, -ep.Setup.EntryLow
		c.Stop, c.Target, c.ProtectedStructure = -ep.Setup.Stop, -ep.Setup.Target, -ep.Setup.ProtectedStructure
		c.Retest.Low = -ep.Setup.Retest.Low
	}
	ep.Setup = &c
	return ep
}

// ref is a reference structure in the bullish frame: a resistance that price
// was below and that this episode's break is a close above.
type ref struct {
	Reference
	value func(i int) float64
}

type detector struct {
	cfg  Config
	bars []market.Candle
	atr  *atrWindow
	ph   []pivot // pivot highs (resistance touches and opposing structure)
	pl   []pivot // pivot lows (protected structure)
	e    int
	pip  float64
}

func newDetector(cfg Config, bars []market.Candle) *detector {
	return &detector{
		cfg: cfg, bars: bars, atr: newATRWindow(bars, cfg.ATRLength, cfg.ATRWindowBars),
		ph: pivotHighs(bars, cfg.PivotBars), pl: pivotLows(bars, cfg.PivotBars), e: len(bars) - 1, pip: cfg.PipSize,
	}
}

// barsBetween is the whole number of M5 periods between two candles' open
// times. Every age and window is measured in time, never in slice positions, so
// a gap in the feed ages a confirmation instead of hiding it.
func (d *detector) barsBetween(from, to int) int {
	return int((d.bars[to].Time - d.bars[from].Time) / m5Seconds)
}

// run returns one episode per break attempt that started within the episode
// lookback. Candidates are the episodes that reached CANDIDATE.
func (d *detector) run() []Episode {
	var out []Episode
	first := d.e
	for first > 1 && d.barsBetween(first-1, d.e) <= d.cfg.EpisodeLookback {
		first--
	}
	for b := first; b <= d.e; b++ {
		atrB, okB := d.atr.at(b)
		// The reference and the break buffer are built from what was knowable
		// BEFORE the break candle: ATR as of the candle before it, so the break
		// candle's own range can never decide whether a reference exists.
		atrPre, okPre := d.atr.at(b - 1)
		if !okB || !okPre {
			continue
		}
		for _, r := range d.referencesBrokenAt(b, atrPre) {
			if ep, ok := d.episode(r, b, atrB, atrPre); ok {
				out = append(out, ep)
			}
		}
	}
	return out
}

func (d *detector) breakBuffer(atr float64) float64 {
	return math.Max(d.cfg.BreakBufferPips*d.pip, d.cfg.BreakBufferATR*atr)
}

// referencesBrokenAt builds, from pivots confirmed STRICTLY before bar b, every
// resistance reference whose break starts at b: the close at b is beyond the
// reference by the buffer and price was on the original side before it.
func (d *detector) referencesBrokenAt(b int, atr float64) []ref {
	if b < 1 {
		return nil
	}
	var eligible []pivot
	for _, p := range d.ph {
		if p.conf < b && d.barsBetween(p.index, b) <= d.cfg.ReferenceLookback {
			eligible = append(eligible, p)
		}
	}
	buf := d.breakBuffer(atr)
	tol := math.Max(d.cfg.LevelClusterPips*d.pip, d.cfg.LevelClusterATR*atr)

	var out []ref
	for _, cluster := range clusterByPrice(eligible, tol, tol*d.cfg.LevelSpanMultiple) {
		if len(cluster) < d.cfg.LevelMinTouches {
			continue
		}
		byIndex := append([]pivot(nil), cluster...)
		sort.Slice(byIndex, func(i, j int) bool { return byIndex[i].index < byIndex[j].index })
		sum := 0.0
		for _, p := range cluster {
			sum += p.price
		}
		price := sum / float64(len(cluster))
		if !d.breaksAt(b, buf, func(int) float64 { return price }, 0, b-d.cfg.PreBreakBars) {
			continue
		}
		out = append(out, ref{
			Reference: Reference{
				Kind: KindKeyLevel, ID: fmt.Sprintf("level:%d", d.bars[byIndex[0].index].Time),
				AnchorTime: d.bars[byIndex[0].index].Time, FormedAt: d.bars[byIndex[d.cfg.LevelMinTouches-1].conf].Time,
				Touches: len(cluster), PriceAtBreak: price,
			},
			value: func(int) float64 { return price },
		})
	}

	// Lines: chronologically adjacent eligible pivot highs.
	for i := 0; i+1 < len(eligible); i++ {
		a, c := eligible[i], eligible[i+1]
		span := c.index - a.index
		if span < d.cfg.LineMinSpanBars {
			continue
		}
		if d.bars[c.index].Time-d.bars[a.index].Time > int64(math.Round(float64(span)*float64(m5Seconds)*d.cfg.LineMaxGapRatio)) {
			continue // a market-closure gap inside the anchor span distorts a per-candle slope
		}
		slope := (c.price - a.price) / float64(span)
		if s := math.Abs(slope) / atr; s < d.cfg.LineMinSlopeATR || s > d.cfg.LineMaxSlopeATR {
			continue
		}
		if d.bars[b].Time-d.bars[c.index].Time > int64(math.Round(float64(b-c.index)*float64(m5Seconds)*d.cfg.LineMaxGapRatio)) {
			continue // a gap between the second anchor and the break distorts the per-candle value
		}
		anchor, second := a, c
		value := func(i int) float64 { return second.price + slope*float64(i-second.index) }
		if !d.breaksAt(b, buf, value, d.cfg.LineWickATR*atr, second.index+1) {
			continue
		}
		touches := 2
		for _, p := range eligible {
			if p.index > second.index && math.Abs(p.price-value(p.index)) <= tol {
				touches++
			}
		}
		out = append(out, ref{
			Reference: Reference{
				Kind: KindTrendline, ID: fmt.Sprintf("line:%d:%d", d.bars[anchor.index].Time, d.bars[second.index].Time),
				AnchorTime: d.bars[anchor.index].Time, FormedAt: d.bars[second.conf].Time,
				Touches: touches, PriceAtBreak: value(b), Slope: slope,
			},
			value: value,
		})
	}
	return out
}

// breaksAt reports whether b is the FIRST beyond-bar of a reference: its close
// is above value(b)+buffer, and every earlier close from `from` to b-1 stayed at
// or below value+buffer (price really was on the original side), with no wick
// above value+wickTolerance in that stretch when wickTolerance > 0.
func (d *detector) breaksAt(b int, buf float64, value func(int) float64, wickTolerance float64, from int) bool {
	if from < 0 {
		from = 0
	}
	if d.bars[b].Close <= value(b)+buf {
		return false
	}
	for i := from; i < b; i++ {
		if d.bars[i].Close > value(i)+buf {
			return false
		}
		if wickTolerance > 0 && d.bars[i].High > value(i)+wickTolerance {
			return false
		}
	}
	return true
}

// clusterByPrice greedily groups pivots by price: ascending price, a pivot joins
// the open cluster only when it is within tol of EVERY member and the cluster's
// span stays within maxSpan.
func clusterByPrice(pivots []pivot, tol, maxSpan float64) [][]pivot {
	sorted := append([]pivot(nil), pivots...)
	sort.SliceStable(sorted, func(i, j int) bool {
		if sorted[i].price != sorted[j].price {
			return sorted[i].price < sorted[j].price
		}
		return sorted[i].index < sorted[j].index
	})
	var clusters [][]pivot
	for _, p := range sorted {
		if n := len(clusters); n > 0 && canJoin(clusters[n-1], p, tol, maxSpan) {
			clusters[n-1] = append(clusters[n-1], p)
		} else {
			clusters = append(clusters, []pivot{p})
		}
	}
	return clusters
}

func canJoin(cluster []pivot, p pivot, tol, maxSpan float64) bool {
	lo, hi := p.price, p.price
	for _, m := range cluster {
		lo, hi = math.Min(lo, m.price), math.Max(hi, m.price)
		if math.Abs(m.price-p.price) > tol {
			return false
		}
	}
	return hi-lo <= maxSpan
}

// episode runs one break attempt through the lifecycle up to the evaluated bar.
func (d *detector) episode(r ref, b int, atrB, atrPre float64) (Episode, bool) {
	cfg, bars, e := d.cfg, d.bars, d.e
	ep := Episode{Reference: r.Reference, BreakStart: bars[b].Time, State: StateBreakPending, EndedAt: bars[b].Time}
	terminal := func(state State, reason string, i int) (Episode, bool) {
		ep.State, ep.Reason, ep.EndedAt = state, reason, bars[i].Time
		return ep, true
	}
	buf := d.breakBuffer(atrPre)

	// Acceptance: k consecutive closes beyond the reference by the buffer.
	k := cfg.BreakoutAcceptBars
	for i := b; i < b+k; i++ {
		if i > e {
			return ep, true // still BREAK_PENDING
		}
		// k CONSECUTIVE closes: every candle of the run follows the previous one
		// by exactly one period, so a feed gap inside the run is not acceptance.
		if i > b && (bars[i].Close <= r.value(i)+buf || bars[i].Time-bars[i-1].Time != m5Seconds) {
			return terminal(StateFalseBreakout, ReasonBreakNotAccepted, i)
		}
	}
	a := b + k - 1
	ep.AcceptedAt, ep.State, ep.EndedAt = bars[a].Time, StateBreakAccepted, bars[a].Time
	brk := Break{
		StartTime: bars[b].Time, AcceptedAt: bars[a].Time, AcceptCloses: k,
		DistanceATR: (bars[a].Close - r.value(a)) / atrB, BodyRatio: bodyRatio(bars[b]), CloseStrength: closeStrength(bars[b]),
		DisplacementATR: (bars[a].Close - bars[b].Open) / atrB,
	}
	if brk.BodyRatio < cfg.MinBreakBodyRatio || brk.CloseStrength < cfg.MinBreakCloseStrength || brk.DisplacementATR < cfg.MinBreakDisplacement {
		return terminal(StateFalseBreakout, ReasonBreakQuality, a)
	}
	protected, protectedAt, ok := d.protectedStructure(b, a, r.value(b))
	if !ok {
		return terminal(StateStructureInvalided, ReasonNoProtected, a)
	}
	// Anything that traded below the protected structure between its pivot and
	// the acceptance (the pivot's own right-hand candles, the base, the break run)
	// already lost it.
	for i := protectedAt + 1; i <= a; i++ {
		if bars[i].Low < protected {
			return terminal(StateStructureInvalided, ReasonProtectedLost, i)
		}
	}

	failBuf := math.Max(cfg.BreakFailPips*d.pip, cfg.BreakFailATR*atrB)
	touchTol := math.Max(cfg.RetestTouchPips*d.pip, cfg.RetestTouchATR*atrB)
	touch, confirm := -1, -1
	for i := a + 1; i <= e; i++ {
		c := bars[i]
		if ep.State == StateBreakAccepted {
			ep.State = StateRetestWaiting
		}
		switch {
		case confirm < 0 && c.Low < protected:
			return terminal(StateStructureInvalided, ReasonProtectedLost, i)
		case confirm < 0 && c.Close < r.value(i)-failBuf:
			if touch < 0 {
				return terminal(StateFalseBreakout, ReasonReclaimedBeforeRetst, i)
			}
			return terminal(StateRetestFailed, ReasonRetestClosedThrough, i)
		case confirm < 0 && touch < 0 && d.barsBetween(a, i) > cfg.RetestMaxBars:
			return terminal(StateExpired, ReasonRetestWindowExpired, i)
		case confirm < 0 && touch >= 0 && d.barsBetween(touch, i) >= cfg.ConfirmationWindowBars:
			return terminal(StateExpired, ReasonConfirmWindowExpired, i)
		}
		if confirm >= 0 {
			break
		}
		if touch < 0 && c.Low <= r.value(i)+touchTol {
			touch = i
			ep.State, ep.EndedAt = StateRetestTouched, c.Time
		}
		if touch >= 0 && d.rejects(c, r.value(i)) {
			confirm = i
			ep.State, ep.EndedAt = StateRetestConfirmed, c.Time
		}
	}
	if confirm < 0 {
		return ep, true
	}
	return d.finish(ep, r, brk, protected, b, a, touch, confirm, atrB, failBuf), true
}

// rejects is the directional rejection of the retested reference: a bullish
// candle closing above it, in the upper part of its range, with either a
// lower-wick reaction or a strong body.
func (d *detector) rejects(c market.Candle, level float64) bool {
	return c.Close > c.Open && c.Close > level && closeStrength(c) >= d.cfg.RejectionCloseStrength &&
		(lowerWickRatio(c) >= d.cfg.RejectionWickRatio || bodyRatio(c) >= d.cfg.RejectionBodyRatio)
}

// protectedStructure is the low whose loss means the break failed.
func (d *detector) protectedStructure(b, a int, level float64) (price float64, at int, ok bool) {
	switch d.cfg.ProtectedStructure {
	case ProtectedBreakOrigin:
		from := b - d.cfg.PreBreakBars
		if from < 0 {
			from = 0
		}
		price, at = math.Inf(1), from
		for i := from; i <= a; i++ {
			if d.bars[i].Low < price {
				price, at = d.bars[i].Low, i
			}
		}
	default:
		at = -1
		for _, p := range d.pl {
			if p.conf < b && d.barsBetween(p.index, b) <= d.cfg.ReferenceLookback && p.index > at {
				at, price = p.index, p.price
			}
		}
		if at < 0 {
			return 0, 0, false
		}
	}
	return price, at, price < level
}

// finish turns a confirmed retest into a candidate, or records exactly why the
// confirmed retest is not published.
func (d *detector) finish(ep Episode, r ref, brk Break, protected float64, b, a, touch, confirm int, atrB, failBuf float64) Episode {
	cfg, bars, e := d.cfg, d.bars, d.e
	reject := func(reason string) Episode {
		ep.State, ep.Reason = StateRetestConfirmed, reason
		return ep
	}
	atrC, ok := d.atr.at(confirm)
	if !ok {
		return reject(ReasonATRUnavailable)
	}
	level := r.value(confirm)
	retestLow := math.Inf(1)
	for i := touch; i <= confirm; i++ {
		retestLow = math.Min(retestLow, bars[i].Low)
	}
	tolE := math.Max(cfg.EntryTolerancePips*d.pip, cfg.EntryToleranceATR*atrC)
	entryLow, entryHigh := level-tolE, level+tolE
	stop := math.Min(retestLow, protected) - math.Max(d.pip, cfg.InvalidationBufferATR*atrC)
	if !(stop < entryLow) || !(entryHigh > entryLow) {
		return reject(ReasonDegenerateStop)
	}
	risk := entryHigh - stop
	riskPips := risk / d.pip

	target, found, anyAbove := d.target(b, confirm, entryHigh, level, atrC, atrB)
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
		case c.Low <= stop || c.Close < r.value(i)-failBuf:
			ep.State, ep.Reason, ep.EndedAt = StateRetestFailed, ReasonInvalidatedAfter, c.Time
			return ep
		case c.High >= target:
			return reject(ReasonTargetReached)
		}
	}
	if d.barsBetween(confirm, e) > cfg.ConfirmationMaxAgeBars {
		return reject(ReasonConfirmationStale)
	}
	last := bars[e]
	if last.Close < entryLow {
		return reject(ReasonPriceThroughEntry)
	}
	if last.Close-entryHigh > cfg.MaximumEntryDistanceATR*atrC {
		return reject(ReasonEntryTooFar)
	}

	cb := bars[confirm]
	setup := Setup{
		Reference: ep.Reference, Break: brk,
		Retest: Retest{
			TouchTime: bars[touch].Time, ConfirmedAt: cb.Time, Low: retestLow,
			DepthATR:  math.Max(0, level-retestLow) / atrC,
			WickRatio: lowerWickRatio(cb), CloseStrength: closeStrength(cb), BarsAfterAcceptance: d.barsBetween(a, confirm),
		},
		EntryLow: entryLow, EntryHigh: entryHigh, Stop: stop, Target: target, ProtectedStructure: protected,
		ATR: atrC, RiskPips: riskPips, RewardRisk: reward / risk, TargetRoomATR: reward / atrC,
		WickRejection: lowerWickRatio(cb) >= cfg.RejectionWickRatio, ConfirmedAt: cb.Time,
	}
	setup.Reference.PriceAtBreak = r.Reference.PriceAtBreak
	ep.State, ep.Reason, ep.Setup = StateCandidate, "", &setup
	return ep
}

// target is the nearest credible opposing structure above the entry: a
// confirmed pivot high that already existed BEFORE the break began (a high made
// by the breakout itself is the move, not a barrier), above everything price has
// traded since the break began and outside the broken zone, heading a swing of at least target_swing_atr. found is false when the nearest
// credible structure leaves less than the required room; anyAbove tells "nothing
// there" from "blocked too close". The nearest decides: a closer credible
// blocker is never skipped in favour of a farther one.
func (d *detector) target(b, confirm int, entryHigh, level, atrC, atrB float64) (price float64, found, anyAbove bool) {
	cfg := d.cfg
	zone := math.Max(cfg.LevelClusterPips*d.pip, cfg.LevelClusterATR*atrB)
	// A swing high price has already traded through since the break began is
	// behind it, not a barrier ahead.
	floor := math.Max(entryHigh, level+zone)
	for j := b; j <= confirm; j++ {
		floor = math.Max(floor, d.bars[j].High)
	}
	closest := math.Inf(1)
	for _, p := range d.ph {
		if p.index >= b || d.barsBetween(p.index, confirm) > cfg.TargetLookbackBars || p.price <= floor {
			continue
		}
		low := math.Inf(1)
		for j := p.index - cfg.TargetSwingBars; j <= p.conf; j++ {
			if j >= 0 {
				low = math.Min(low, d.bars[j].Low)
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
