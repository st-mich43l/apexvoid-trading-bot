package breakretest

import (
	"fmt"
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
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
// tests hand it more to prove that is enough. The structure frame is M5 here.
func analyseWindow(cfg Config, window []market.Candle) Analysis {
	return resolve(detectRaw(cfg, window, m5Seconds, window), nil)
}

// detectRaw is every episode as of the last candle, with every CANDIDATE still a
// candidate: nothing is yet chosen between references broken in the same move.
// The reference, its break and acceptance and the protected structure are read on
// structure (candles of period seconds, closed bars only); the retest, its
// rejection, the stop and the entry are read on fine (M5). When the structure
// frame is M5, structure and fine are the same series.
func detectRaw(cfg Config, structure []market.Candle, period int64, fine []market.Candle) []Episode {
	if len(structure) == 0 || len(fine) == 0 || !validSeries(structure) || !validSeries(fine) {
		return nil
	}
	same := period == m5Seconds && len(structure) == len(fine) && &structure[0] == &fine[0]
	var episodes []Episode
	for _, side := range []market.Direction{market.Buy, market.Sell} {
		oriented, orientedFine := structure, fine
		if side == market.Sell {
			oriented = mirror(structure)
			orientedFine = oriented
			if !same {
				orientedFine = mirror(fine)
			}
		} else if same {
			orientedFine = oriented
		}
		d := newDetector(cfg, oriented, period, orientedFine)
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
	// value is the reference at structure-frame bar i; at is the same line at a time (the retest reads it on M5 bars).
	value func(i int) float64
	at    func(t int64) float64
}

type detector struct {
	cfg  Config
	bars []market.Candle // the structure frame (closed bars of period seconds)
	atr  *indicator.WilderWindow
	ph   []pivot // pivot highs (resistance touches and opposing structure)
	pl   []pivot // pivot lows (protected structure)
	e    int
	pip  float64
	// period is the structure frame's bar length in seconds; fine is the M5 series the retest and its rejection are read on, fineATR its ATR. When the structure
	// frame is M5, fine is bars.
	period  int64
	fine    []market.Candle
	fineATR *indicator.WilderWindow
}

func newDetector(cfg Config, bars []market.Candle, period int64, fine []market.Candle) *detector {
	d := &detector{
		cfg: cfg, bars: bars, atr: newATRWindow(bars, cfg.ATRLength, cfg.ATRWindowBars),
		ph: pivotHighs(bars, cfg.PivotBars), pl: pivotLows(bars, cfg.PivotBars), e: len(bars) - 1, pip: cfg.PipSize,
		period: period, fine: fine,
	}
	d.fineATR = d.atr
	if period != m5Seconds || len(fine) != len(bars) || (len(bars) > 0 && &fine[0] != &bars[0]) {
		d.fineATR = newATRWindow(fine, cfg.ATRLength, cfg.ATRWindowBars)
	}
	return d
}

// barsBetween is the whole number of structure-frame periods between two
// structure candles' open times. Every age and window is measured in time, never
// in slice positions, so a gap in the feed ages a confirmation instead of hiding it.
func (d *detector) barsBetween(from, to int) int {
	return int((d.bars[to].Time - d.bars[from].Time) / d.period)
}

// fineFrom is the first M5 candle that opens at or after t: the earliest bar that can know a structure-frame bar that closed at t.
func (d *detector) fineFrom(t int64) int {
	return sort.Search(len(d.fine), func(i int) bool { return d.fine[i].Time >= t })
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
		atrB, okB := d.atr.At(b)
		// The reference and the break buffer are built from what was knowable
		// BEFORE the break candle: ATR as of the candle before it, so the break
		// candle's own range can never decide whether a reference exists.
		atrPre, okPre := d.atr.At(b - 1)
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
			at:    func(int64) float64 { return price },
		})
	}

	// Lines: chronologically adjacent eligible pivot highs.
	for i := 0; i+1 < len(eligible); i++ {
		a, c := eligible[i], eligible[i+1]
		span := c.index - a.index
		if span < d.cfg.LineMinSpanBars {
			continue
		}
		if d.bars[c.index].Time-d.bars[a.index].Time > int64(math.Round(float64(span)*float64(d.period)*d.cfg.LineMaxGapRatio)) {
			continue // a market-closure gap inside the anchor span distorts a per-candle slope
		}
		slope := (c.price - a.price) / float64(span)
		if s := math.Abs(slope) / atr; s < d.cfg.LineMinSlopeATR || s > d.cfg.LineMaxSlopeATR {
			continue
		}
		if d.bars[b].Time-d.bars[c.index].Time > int64(math.Round(float64(b-c.index)*float64(d.period)*d.cfg.LineMaxGapRatio)) {
			continue // a gap between the second anchor and the break distorts the per-candle value
		}
		anchor, second := a, c
		value := func(i int) float64 { return second.price + slope*float64(i-second.index) }
		secondTime, period := d.bars[second.index].Time, float64(d.period)
		at := func(t int64) float64 { return second.price + slope*float64(t-secondTime)/period }
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
			value: value, at: at,
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
		if i > b && (bars[i].Close <= r.value(i)+buf || bars[i].Time-bars[i-1].Time != d.period) {
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
	// The retest and its rejection are read on M5, from the first M5 bar that opens once the acceptance bar has closed: a bar still forming on the structure frame
	// is never seen. The retest window is counted in structure-frame bars, converted to time; the touch-to-rejection window is M5 bars (the rejection is an M5 reading).
	fine := d.fine
	retestFrom := d.fineFrom(bars[a].Time + d.period)
	fineTerminal := func(state State, reason string, j int) (Episode, bool) {
		ep.State, ep.Reason, ep.EndedAt = state, reason, fine[j].Time
		return ep, true
	}
	touch, confirm := -1, -1
	for j := retestFrom; j < len(fine); j++ {
		c := fine[j]
		atrJ, okJ := d.fineATR.At(j)
		if !okJ {
			continue
		}
		level := r.at(c.Time)
		if ep.State == StateBreakAccepted {
			ep.State = StateRetestWaiting
		}
		switch {
		case confirm < 0 && c.Low < protected:
			return fineTerminal(StateStructureInvalided, ReasonProtectedLost, j)
		case confirm < 0 && c.Close < level-failBuf:
			if touch < 0 {
				return fineTerminal(StateFalseBreakout, ReasonReclaimedBeforeRetst, j)
			}
			return fineTerminal(StateRetestFailed, ReasonRetestClosedThrough, j)
		case confirm < 0 && touch < 0 && int((c.Time-bars[a].Time)/d.period) > cfg.RetestMaxBars:
			return fineTerminal(StateExpired, ReasonRetestWindowExpired, j)
		case confirm < 0 && touch >= 0 && int((c.Time-fine[touch].Time)/m5Seconds) >= cfg.ConfirmationWindowBars:
			return fineTerminal(StateExpired, ReasonConfirmWindowExpired, j)
		}
		if confirm >= 0 {
			break
		}
		touchTol := math.Max(cfg.RetestTouchPips*d.pip, cfg.RetestTouchATR*atrJ)
		if touch < 0 && c.Low <= level+touchTol {
			touch = j
			ep.State, ep.EndedAt = StateRetestTouched, c.Time
		}
		// Optional confirmation: the first touch of the broken reference is the
		// trigger; the rejecting candle is still measured (Setup.Rejected) but
		// no longer required.
		if touch >= 0 && (d.rejects(c, level) || cfg.CandleConfirmationOptional && j == touch) {
			confirm = j
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
// confirmed retest is not published. touch and confirm are M5 indexes; b and a
// are structure-frame indexes.
func (d *detector) finish(ep Episode, r ref, brk Break, protected float64, b, a, touch, confirm int, atrB, failBuf float64) Episode {
	cfg, bars, fine := d.cfg, d.bars, d.fine
	last := len(fine) - 1
	reject := func(reason string) Episode {
		ep.State, ep.Reason = StateRetestConfirmed, reason
		return ep
	}
	atrC, ok := d.fineATR.At(confirm)
	if !ok {
		return reject(ReasonATRUnavailable)
	}
	atrS, ok := d.atr.At(a)
	if !ok {
		return reject(ReasonATRUnavailable)
	}
	level := r.at(fine[confirm].Time)
	retestLow := math.Inf(1)
	for i := touch; i <= confirm; i++ {
		retestLow = math.Min(retestLow, fine[i].Low)
	}
	// The entry tolerance and the stop buffer are M5 quantities: the stop sits beyond the M5 retest extreme and the protected structure.
	tolE := math.Max(cfg.EntryTolerancePips*d.pip, cfg.EntryToleranceATR*atrC)
	entryLow, entryHigh := level-tolE, level+tolE
	stop := math.Min(retestLow, protected) - math.Max(d.pip, cfg.InvalidationBufferATR*atrC)
	if !(stop < entryLow) || !(entryHigh > entryLow) {
		return reject(ReasonDegenerateStop)
	}
	risk := entryHigh - stop
	riskPips := risk / d.pip

	target, found, anyAbove := d.target(b, a, confirm, entryHigh, level, atrS, atrB)
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

	for i := confirm + 1; i <= last; i++ {
		c := fine[i]
		switch {
		case c.Low <= stop || c.Close < r.at(c.Time)-failBuf:
			ep.State, ep.Reason, ep.EndedAt = StateRetestFailed, ReasonInvalidatedAfter, c.Time
			return ep
		case c.High >= target:
			return reject(ReasonTargetReached)
		}
	}
	// A confirmation is fresh for confirmation_max_age_bars structure-frame bars, converted to time.
	if int((fine[last].Time-fine[confirm].Time)/d.period) > cfg.ConfirmationMaxAgeBars {
		return reject(ReasonConfirmationStale)
	}
	lastBar := fine[last]
	if lastBar.Close < entryLow {
		return reject(ReasonPriceThroughEntry)
	}
	if lastBar.Close-entryHigh > cfg.MaximumEntryDistanceATR*atrC {
		return reject(ReasonEntryTooFar)
	}

	cb := fine[confirm]
	setup := Setup{
		Reference: ep.Reference, Break: brk,
		Retest: Retest{
			TouchTime: fine[touch].Time, ConfirmedAt: cb.Time, Low: retestLow,
			DepthATR:  math.Max(0, level-retestLow) / atrC,
			WickRatio: lowerWickRatio(cb), CloseStrength: closeStrength(cb), BarsAfterAcceptance: int((cb.Time - bars[a].Time) / d.period),
		},
		EntryLow: entryLow, EntryHigh: entryHigh, Stop: stop, Target: target, ProtectedStructure: protected,
		ATR: atrC, StructureATR: atrS, ThesisBand: math.Max(cfg.LevelClusterPips*d.pip, cfg.LevelClusterATR*atrB),
		RiskPips: riskPips, RewardRisk: reward / risk, TargetRoomATR: reward / atrC,
		Rejected:      d.rejects(cb, r.at(cb.Time)),
		WickRejection: lowerWickRatio(cb) >= cfg.RejectionWickRatio && d.rejects(cb, r.at(cb.Time)), ConfirmedAt: cb.Time,
	}
	setup.Reference.PriceAtBreak = r.Reference.PriceAtBreak
	ep.State, ep.Reason, ep.Setup = StateCandidate, "", &setup
	return ep
}

// target is the nearest credible opposing structure above the entry: a
// confirmed pivot high of the structure frame that already existed BEFORE the
// break began (a high made by the breakout itself is the move, not a barrier),
// above everything price has traded since the break began and outside the broken
// zone, heading a swing of at least target_swing_atr structure-frame ATRs. found
// is false when the nearest credible structure leaves less than the required
// room (in structure-frame ATRs); anyAbove tells "nothing there" from "blocked
// too close". The nearest decides: a closer credible blocker is never skipped in
// favour of a farther one.
func (d *detector) target(b, a, confirm int, entryHigh, level, atrS, atrB float64) (price float64, found, anyAbove bool) {
	cfg := d.cfg
	zone := math.Max(cfg.LevelClusterPips*d.pip, cfg.LevelClusterATR*atrB)
	// A swing high price has already traded through since the break began is
	// behind it, not a barrier ahead: the structure bars of the break and the M5
	// bars since the acceptance closed.
	floor := math.Max(entryHigh, level+zone)
	for j := b; j <= a; j++ {
		floor = math.Max(floor, d.bars[j].High)
	}
	for j := d.fineFrom(d.bars[a].Time + d.period); j <= confirm; j++ {
		floor = math.Max(floor, d.fine[j].High)
	}
	confirmTime := d.fine[confirm].Time
	closest := math.Inf(1)
	for _, p := range d.ph {
		if p.index >= b || int((confirmTime-d.bars[p.index].Time)/d.period) > cfg.TargetLookbackBars || p.price <= floor {
			continue
		}
		low := math.Inf(1)
		for j := p.index - cfg.TargetSwingBars; j <= p.conf; j++ {
			if j >= 0 {
				low = math.Min(low, d.bars[j].Low)
			}
		}
		if p.price-low >= cfg.TargetSwingATR*atrS && p.price < closest {
			closest = p.price
		}
	}
	if math.IsInf(closest, 1) {
		return 0, false, false
	}
	if closest-entryHigh < cfg.MinimumTargetRoomATR*atrS {
		return 0, false, true
	}
	return closest, true, true
}
