package confluencezone

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
)

const (
	epsilon = 1e-9
	// confluenceBump is the frozen technique contract's score bonus for every
	// additional source supporting a band (confluence_technique_bonus_score). It
	// is now earned per independent source, not per label.
	confluenceBump = 2.5
)

// Detect finds the confirmed confluence reactions and resting overlaps in the
// closed candles, from the structures already derived (Input.Facts).
//
// Everything is decided from candles up to and including the last one, by
// closed-candle timestamps: a structure counts for a reaction only if it was
// confirmable before the touch candle opened, so the same decision is reached
// from the prefix ending at the evaluated candle and from any longer history,
// and later candles cannot change it. Both sides are evaluated by one long-side
// routine on mirrored prices, so BUY and SELL are exact mirror images.
func Detect(cfg Config, in Input) Analysis {
	a := Analysis{Facts: in.Facts}
	last := len(in.Bars) - 1
	if last < 0 || in.PeriodSeconds <= 0 {
		return a
	}
	if len(in.Bars) <= cfg.ReactionLookback {
		a.Rejections = append(a.Rejections, Rejection{Reason: ReasonInsufficientHistory})
		return a
	}
	if !(in.ATR > 0) || math.IsNaN(in.ATR) || math.IsInf(in.ATR, 0) {
		a.Rejections = append(a.Rejections, Rejection{Reason: ReasonATRUnavailable})
		return a
	}
	// A series with a duplicate or out-of-order candle is not a market history:
	// nothing is decided from it, and a gap is never mistaken for a fresh candle.
	for i := 1; i <= last; i++ {
		if in.Bars[i].Time <= in.Bars[i-1].Time {
			a.Rejections = append(a.Rejections, Rejection{Reason: ReasonCandleSeries, TouchTime: in.Bars[i].Time})
			return a
		}
	}
	atr, pip := in.ATR, cfg.PipSize
	params := structuralconfluence.Params{
		MinimumFacts: cfg.MinimumFacts,
		MinimumWidth: math.Max(cfg.MinimumOverlapPips*pip, cfg.MinimumOverlapATR*atr),
	}
	// The reaction window is measured in time, not in array positions: after a
	// gap in the data (a closed market) an old candle is never fresh again.
	cutoff := in.Bars[last].Time - int64(cfg.ReactionLookback-1)*in.PeriodSeconds
	windowStart := last
	for windowStart > 0 && in.Bars[windowStart-1].Time >= cutoff {
		windowStart--
	}

	type sideRun struct {
		side      string
		direction market.Direction
		other     string
	}
	runs := []sideRun{{structuralconfluence.Buy, market.Buy, structuralconfluence.Sell}, {structuralconfluence.Sell, market.Sell, structuralconfluence.Buy}}
	for _, run := range runs {
		now, overlapRejects := structuralconfluence.Find(in.Facts, run.side, structuralconfluence.NoCutoff, params)
		a.Regions = append(a.Regions, now...)
		for _, r := range overlapRejects {
			a.Rejections = append(a.Rejections, Rejection{Direction: run.direction, Reason: r.Reason, Low: r.Low, High: r.High, FactIDs: r.FactIDs})
		}
		opposing, _ := structuralconfluence.Find(in.Facts, run.other, structuralconfluence.NoCutoff, params)

		v := newView(run.side == structuralconfluence.Sell, in, opposing)
		regionsAt := map[int][]structuralconfluence.Region{}
		at := func(ti int) []structuralconfluence.Region {
			if r, ok := regionsAt[ti]; ok {
				return r
			}
			r, _ := structuralconfluence.Find(in.Facts, run.side, in.Bars[ti].Time, params)
			regionsAt[ti] = r
			return r
		}
		seen := map[string]bool{}
		accepted := map[string]bool{}
		note := func(r structuralconfluence.Region, reason string, ti, ci int) {
			key := r.EpisodeID + "|" + reason + "|" + itoa(int64(ci))
			if seen[key] {
				return
			}
			seen[key] = true
			a.Rejections = append(a.Rejections, Rejection{
				Direction: run.direction, Reason: reason, EpisodeID: r.EpisodeID, Low: r.Low, High: r.High,
				TouchTime: in.Bars[ti].Time, ConfirmTime: in.Bars[ci].Time, FactIDs: r.Support,
			})
		}
		for ci := last; ci >= windowStart; ci-- {
			for ti := ci; ti >= windowStart; ti-- {
				// A region that exists now and that this candle touched, but that no
				// structure set confirmable before the candle opened supports, was
				// built from a structure created by the reaction itself or later.
				for _, r := range now {
					if touched(in.Bars[ti], r) && !supportedAt(at(ti), r) {
						note(r, ReasonStructureNotAvailable, ti, ci)
					}
				}
				for _, r := range at(ti) {
					if !touched(in.Bars[ti], r) {
						continue
					}
					if accepted[r.EpisodeID+"|"+itoa(int64(ci))] {
						continue
					}
					setup, reason := v.evaluate(cfg, run.direction, r, in, ti, ci)
					if reason != "" {
						note(r, reason, ti, ci)
						continue
					}
					accepted[r.EpisodeID+"|"+itoa(int64(ci))] = true
					a.Setups = append(a.Setups, setup)
				}
			}
		}
		for _, r := range now {
			if !touched(in.Bars[last], r) {
				continue
			}
			if resting, ok := v.resting(cfg, run.direction, r, in); ok {
				a.Resting = append(a.Resting, resting)
			}
		}
	}
	sort.SliceStable(a.Setups, func(i, j int) bool {
		x, y := a.Setups[i], a.Setups[j]
		switch {
		case x.ConfirmIndex != y.ConfirmIndex:
			return x.ConfirmIndex > y.ConfirmIndex
		case x.IndependentFacts != y.IndependentFacts:
			return x.IndependentFacts > y.IndependentFacts
		case x.TouchIndex != y.TouchIndex:
			return x.TouchIndex > y.TouchIndex
		}
		return x.Region.EpisodeID < y.Region.EpisodeID
	})
	sort.SliceStable(a.Rejections, func(i, j int) bool {
		x, y := a.Rejections[i], a.Rejections[j]
		switch {
		case x.Direction != y.Direction:
			return x.Direction < y.Direction
		case x.Reason != y.Reason:
			return x.Reason < y.Reason
		case x.EpisodeID != y.EpisodeID:
			return x.EpisodeID < y.EpisodeID
		}
		return x.ConfirmTime < y.ConfirmTime
	})
	return a
}

func touched(b market.Candle, r structuralconfluence.Region) bool {
	return b.Low <= r.High+epsilon && b.High >= r.Low-epsilon
}

// supportedAt reports whether any region of the set overlaps r with positive width.
func supportedAt(regions []structuralconfluence.Region, r structuralconfluence.Region) bool {
	for _, other := range regions {
		if _, _, width := structuralconfluence.Overlap(r.Low, r.High, other.Low, other.High); width > 0 {
			return true
		}
	}
	return false
}

// view evaluates one side. For a SELL every price is negated, so the routine
// below only ever reasons about a long: a demand region price rejects upward.
type view struct {
	sell bool
	bars []market.Candle // mirrored for a sell
	// targets are the opposing liquidity levels above the trade; blockers the near
	// edges of opposing confluence regions, both in view coordinates.
	targets  []float64
	blockers []float64
	price    float64
}

func newView(sell bool, in Input, opposing []structuralconfluence.Region) *view {
	v := &view{sell: sell, bars: make([]market.Candle, len(in.Bars))}
	for i, b := range in.Bars {
		v.bars[i] = v.candle(b)
	}
	want := "buy"
	if sell {
		want = "sell"
	}
	for _, p := range in.Pools {
		if p.Side == want {
			v.targets = append(v.targets, v.p(p.Level))
		}
	}
	for _, r := range opposing {
		// The near edge of an opposing region: its low when buying, high when selling.
		edge := r.Low
		if sell {
			edge = r.High
		}
		v.blockers = append(v.blockers, v.p(edge))
	}
	v.price = v.bars[len(v.bars)-1].Close
	return v
}

func (v *view) p(price float64) float64 {
	if v.sell {
		return -price
	}
	return price
}

func (v *view) candle(c market.Candle) market.Candle {
	if !v.sell {
		return c
	}
	c.Open, c.High, c.Low, c.Close = -c.Open, -c.Low, -c.High, -c.Close
	return c
}

// supportFar is the far edge of the supporting structures in view coordinates:
// the lowest low for a long, the (negated) highest high for a short.
func (v *view) supportFar(support []structuralconfluence.Fact) float64 {
	far := math.Inf(1)
	for _, f := range support {
		edge := f.Low
		if v.sell {
			edge = -f.High
		}
		far = math.Min(far, edge)
	}
	return far
}

func (v *view) region(r structuralconfluence.Region) (lo, hi float64) {
	if v.sell {
		return -r.High, -r.Low
	}
	return r.Low, r.High
}

// supportFacts returns the facts of a region's support set.
func supportFacts(facts []structuralconfluence.Fact, ids []string) []structuralconfluence.Fact {
	want := make(map[string]bool, len(ids))
	for _, id := range ids {
		want[id] = true
	}
	var out []structuralconfluence.Fact
	for _, f := range facts {
		if want[f.ID] {
			out = append(out, f)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// evaluate decides one (region, touch, confirmation) in view coordinates and
// returns either a setup (in real prices) or the reason it is not one.
func (v *view) evaluate(cfg Config, direction market.Direction, r structuralconfluence.Region, in Input, ti, ci int) (Setup, string) {
	atr, pip := in.ATR, cfg.PipSize
	lo, hi := v.region(r)
	last := len(v.bars) - 1
	support := supportFacts(in.Facts, r.Support)
	far := v.supportFar(support)

	// Price must not have been accepted below the region between the touch and
	// the confirming candle: that is the structure failing, not rejecting.
	for k := ti; k < ci; k++ {
		if v.bars[k].Close < lo-epsilon {
			return Setup{}, ReasonAcceptedThrough
		}
	}
	rx, reason := v.measure(cfg, ti, ci, lo, hi, atr)
	if reason != "" {
		return Setup{}, reason
	}
	// Nothing after the confirmation may have closed beyond what the reaction
	// itself traded to: the rejection has failed if it did.
	for k := ci + 1; k <= last; k++ {
		if v.bars[k].Close < rx.Adverse-epsilon {
			return Setup{}, ReasonFailedAfter
		}
	}

	geo, reason := v.geometry(cfg, lo, hi, far, rx.Adverse, ti, last, atr, pip)
	if reason != "" {
		return Setup{}, reason
	}
	// Entry validity: price has neither run through the entry band nor away from it.
	if v.price < geo.entryLow-epsilon {
		return Setup{}, ReasonPriceThroughEntry
	}
	if v.price-geo.entryHigh > cfg.MaximumEntryATR*atr+epsilon {
		return Setup{}, ReasonEntryTooFar
	}

	setup := Setup{
		Direction: direction, Region: r, Support: support,
		TouchIndex: ti, ConfirmIndex: ci, TouchTime: in.Bars[ti].Time, ConfirmTime: in.Bars[ci].Time,
		Reaction: rx.real(v), ATR: atr, RiskPips: geo.risk / pip, RewardRisk: geo.reward / geo.risk, TargetRoomATR: geo.reward / atr,
		IndependentFacts: r.MinGroups, DependencyCount: len(support) - len(r.Groups),
		OverlapWidthATR: (hi - lo) / atr, OverlapWidthPips: (hi - lo) / pip, Freshness: last - ci,
	}
	setup.EntryLow, setup.EntryHigh = v.realBand(geo.entryLow, geo.entryHigh)
	setup.ProtectedFar, setup.Stop, setup.Target = v.p(far), v.p(geo.stop), v.p(geo.target)
	v.measureStructure(&setup, cfg, in, support, r, lo, hi, ti)
	return setup, ""
}

func (v *view) realBand(low, high float64) (float64, float64) {
	if v.sell {
		return -high, -low
	}
	return low, high
}

// geometry is the entry band, the honest stop and the target of one reaction.
type geometry struct {
	entryLow, entryHigh float64
	stop, target        float64
	risk, reward        float64
}

func (v *view) geometry(cfg Config, lo, hi, far, adverse float64, ti, last int, atr, pip float64) (geometry, string) {
	var g geometry
	g.entryLow, g.entryHigh = lo, hi
	if slice := cfg.EntryMaxWidthPrice; slice > 0 && hi-lo > slice+epsilon {
		g.entryLow = hi - slice
	}
	ref := g.entryHigh
	// The stop protects the structures that make the confluence and the extreme
	// the reaction itself traded to, with a buffer: never tightened into either.
	g.stop = math.Min(far, adverse) - math.Max(pip, cfg.InvalidationBufferATR*atr)
	if !(g.stop < g.entryLow) {
		return g, ReasonDegenerateStop
	}
	g.risk = ref - g.stop
	if cfg.ExecutionStopMaxPips > 0 && g.risk/pip > cfg.ExecutionStopMaxPips+epsilon {
		return g, ReasonRiskEnvelope
	}
	// The target is the nearest opposing liquidity beyond everything traded since
	// the touch and at least the minimum distance from the entry; none is made up.
	floor := ref
	for k := ti; k <= last; k++ {
		floor = math.Max(floor, v.bars[k].High)
	}
	nearest, anyBeyond := math.Inf(1), false
	for _, level := range v.targets {
		if level <= floor+epsilon {
			continue
		}
		anyBeyond = true
		if level-ref >= cfg.MinimumTargetATR*atr-epsilon && level < nearest {
			nearest = level
		}
	}
	switch {
	case math.IsInf(nearest, 1) && !anyBeyond:
		return g, ReasonNoLiquidity
	case math.IsInf(nearest, 1):
		return g, ReasonInsufficientRoom
	}
	g.target = nearest
	g.reward = g.target - ref
	if g.reward/g.risk < cfg.MinimumRewardRisk-1e-12 {
		return g, ReasonRewardRisk
	}
	// A confluence defended the other way, closer than the room the trade needs,
	// blocks it.
	for _, edge := range v.blockers {
		if edge > ref && edge < g.target && edge-ref < cfg.MinimumRewardRisk*g.risk {
			return g, ReasonBlockedByStructure
		}
	}
	return g, ""
}

// reactionView is Reaction in view coordinates; real() maps the adverse extreme back.
type reactionView struct {
	Reaction
}

func (r reactionView) real(v *view) Reaction {
	out := r.Reaction
	out.Adverse = v.p(r.Adverse)
	return out
}

// measure decides whether candle ci confirms a rejection of the long-side
// region [lo, hi] that candle ti touched, from the candle's own measured facts.
//
//   - sweep_reclaim: the wick goes through the region's far edge by at least the
//     sweep minimum and the candle closes back inside (or above) the region by
//     the reclaim minimum;
//   - wick_rejection: the wick reaches the region without sweeping its far edge,
//     and the candle closes away from the region;
//   - strong_reclaim: a displacement candle (a strong bullish body of real range)
//     closes through the region.
//
// The first two need a lower wick of at least the wick ratio and a close in the
// upper (1 - close strength) of the range: the colour of the body is not part of
// the test, only where the candle rejected and where it closed.
func (v *view) measure(cfg Config, ti, ci int, lo, hi, atr float64) (reactionView, string) {
	c := v.bars[ci]
	rng := c.High - c.Low
	if !(rng > epsilon) {
		return reactionView{}, ReasonNoConfirmation
	}
	pip := cfg.PipSize
	adverse := math.Inf(1)
	for k := ti; k <= ci; k++ {
		adverse = math.Min(adverse, v.bars[k].Low)
	}
	bodyLow := math.Min(c.Open, c.Close)
	rx := reactionView{Reaction{
		WickRatio: (bodyLow - c.Low) / rng, CloseStrength: (c.Close - c.Low) / rng, BodyRatio: math.Abs(c.Close-c.Open) / rng,
		RangeATR: rng / atr, SweepDepthATR: math.Max(0, lo-c.Low) / atr, ReclaimDepthATR: (c.Close - lo) / atr, Adverse: adverse,
	}}
	sweepMin := math.Max(cfg.MinimumSweepPips*pip, cfg.MinimumSweepATR*atr)
	wickOK := rx.WickRatio >= cfg.RejectionWickRatio-epsilon
	closeOK := rx.CloseStrength >= cfg.RejectionCloseStrength-epsilon
	switch {
	case c.Low <= lo-sweepMin+epsilon && wickOK && closeOK && c.Close >= lo+cfg.MinimumReclaimATR*atr-epsilon:
		rx.Pattern = reaction.TypeSweepReclaim
	case c.Low > lo-sweepMin+epsilon && c.Low <= hi+epsilon && wickOK && closeOK && c.Close >= lo-epsilon:
		rx.Pattern = reaction.TypeWickRejection
	case c.Close > c.Open && rx.BodyRatio >= cfg.DisplacementBodyRatio-epsilon && rx.RangeATR >= cfg.DisplacementRangeATR-epsilon && closeOK && c.Close > hi+epsilon:
		rx.Pattern = reaction.TypeStrongReclaim
	default:
		return reactionView{}, ReasonNoConfirmation
	}
	return rx, ""
}

// measureStructure fills the measured quality of the supporting structures.
func (v *view) measureStructure(s *Setup, cfg Config, in Input, support []structuralconfluence.Fact, r structuralconfluence.Region, lo, hi float64, ti int) {
	untouched, newest := 0, int64(0)
	best := 0.0
	for _, f := range support {
		if f.Touches == 0 {
			untouched++
		}
		if f.Touches > s.MaxSourceTouches {
			s.MaxSourceTouches = f.Touches
		}
		if f.AvailableAt > newest {
			newest = f.AvailableAt
		}
		best = math.Max(best, f.Score)
	}
	if len(support) > 0 {
		s.SourceIntegrity = float64(untouched) / float64(len(support))
	}
	s.SourceAgeBars = float64(in.Bars[ti].Time-newest) / float64(in.PeriodSeconds)
	s.RegionScore = best + confluenceBump*float64(maxInt(0, len(r.Groups)-1))
	// How much of the region price had already traded through, on a close, between
	// the moment the structures were all confirmable and the touch.
	s.OverlapIntegrity = 1
	if width := hi - lo; width > 0 {
		deepest := hi
		for k, b := range v.bars[:ti] {
			if in.Bars[k].Time+in.PeriodSeconds > newest {
				deepest = math.Min(deepest, b.Close)
			}
		}
		s.OverlapIntegrity = 1 - math.Min(width, math.Max(0, hi-deepest))/width
	}
}

// resting builds the unconfirmed observation of a region price is at.
func (v *view) resting(cfg Config, direction market.Direction, r structuralconfluence.Region, in Input) (Resting, bool) {
	last := len(v.bars) - 1
	atr, pip := in.ATR, cfg.PipSize
	lo, hi := v.region(r)
	far := v.supportFar(supportFacts(in.Facts, r.Support))
	g, reason := v.geometry(cfg, lo, hi, far, v.bars[last].Low, last, last, atr, pip)
	if reason != "" {
		return Resting{}, false
	}
	low, high := v.realBand(g.entryLow, g.entryHigh)
	return Resting{Direction: direction, Region: r, EntryLow: low, EntryHigh: high, Stop: v.p(g.stop), Target: v.p(g.target), ATR: atr}, true
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func itoa(v int64) string {
	if v == 0 {
		return "0"
	}
	neg := v < 0
	if neg {
		v = -v
	}
	var b [20]byte
	i := len(b)
	for v > 0 {
		i--
		b[i] = byte('0' + v%10)
		v /= 10
	}
	if neg {
		i--
		b[i] = '-'
	}
	return string(b[i:])
}
