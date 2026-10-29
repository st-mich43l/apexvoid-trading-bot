package asiarange

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const (
	m5Seconds  = int64(300)
	m15Seconds = int64(900)
)

// RawAnchor is the finalized Asia range as the session read has it: its extremes and the instant it became knowable (London open).
type RawAnchor struct {
	High, Low   float64
	AvailableAt int64
}

// Input is the closed-candle data one evaluation reads, oldest first, plus the anchor and the engine's liquidity read.
type Input struct {
	M5, M15 []market.Candle
	Anchor  *RawAnchor
	// Liquidity is the nearest unswept objective in the trade direction at least minDistance from `from` (prices in the market's own orientation).
	Liquidity func(direction market.Direction, from, minDistance float64) (price float64, ok bool)
}

// Detect evaluates both Asia Range setups as of the last M5 candle.
func Detect(cfg Config, in Input) Analysis { return DetectAsOf(cfg, in, len(in.M5)-1) }

// DetectAsOf is Detect as of M5 index asOf: candles after it (and M15 candles not yet closed by then) are never read.
func DetectAsOf(cfg Config, in Input, asOf int) Analysis {
	var out Analysis
	if asOf < 0 || asOf >= len(in.M5) {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInvalidCandles})
		return out
	}
	m5 := in.M5[:asOf+1]
	evalClose := m5[asOf].Time + m5Seconds
	out.EvaluatedAt = evalClose
	if in.Anchor == nil || in.Anchor.AvailableAt > evalClose || !(in.Anchor.High > in.Anchor.Low) {
		return out
	}
	// Only M15 candles that have fully closed by the evaluated M5 close exist.
	m15 := in.M15
	m15 = m15[:sort.Search(len(m15), func(i int) bool { return m15[i].Time+m15Seconds > evalClose })]
	if !market.ValidSeries(m5) || !market.ValidSeries(m15) {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInvalidCandles})
		return out
	}
	atr5, atr15 := indicator.NewWilderWindow(m5, cfg.ATRLength, cfg.ATRWindowBars), indicator.NewWilderWindow(m15, cfg.ATRLength, cfg.ATRWindowBars)
	if !atr5.Enough() {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInsufficientM5History})
		return out
	}

	// The range is qualified by its width in M15 ATR as of London open.
	known := sort.Search(len(m15), func(i int) bool { return m15[i].Time+m15Seconds > in.Anchor.AvailableAt }) - 1
	rangeATR, ok := atr15.At(known)
	if !atr15.Enough() || !ok {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInsufficientM15History, At: in.Anchor.AvailableAt})
		return out
	}
	anchor := Anchor{High: in.Anchor.High, Low: in.Anchor.Low, AvailableAt: in.Anchor.AvailableAt, RangeATR: (in.Anchor.High - in.Anchor.Low) / rangeATR}
	out.Anchored = true
	windowEnd := anchor.AvailableAt + int64(cfg.ActiveWindowHours*3600)
	horizon := int64(cfg.ReclaimMaxBars+cfg.MSSMaxBars+cfg.AcceptanceCloses+cfg.RetestMaxBars+cfg.ConfirmationMaxAgeBars+2) * m5Seconds
	if evalClose > windowEnd+horizon {
		return out // no episode that started inside the window can still be fresh
	}
	if anchor.RangeATR < cfg.MinimumRangeATR {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonRangeTooNarrow, At: anchor.AvailableAt})
		return out
	}
	if anchor.RangeATR > cfg.MaximumRangeATR {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonRangeTooWide, At: anchor.AvailableAt})
		return out
	}
	start := sort.Search(len(m5), func(i int) bool { return m5[i].Time >= anchor.AvailableAt })
	end := sort.Search(len(m5), func(i int) bool { return m5[i].Time >= windowEnd }) - 1 // the last candle that may start an episode
	if start > asOf || end < start {
		return out
	}

	byEdge := map[string][]episode{}
	for _, e := range []struct {
		edge string
		buy  bool
	}{{EdgeHigh, false}, {EdgeLow, true}} {
		// A stop run through the low is a BUY, through the high a SELL; an accepted break of the high is a BUY, of the low a SELL.
		for _, kind := range []string{KindSweepReclaim, KindBreakRetest} {
			buy := e.buy
			if kind == KindBreakRetest {
				buy = !buy
			}
			d := &detector{cfg: cfg, in: in, anchor: anchor, edge: e.edge, kind: kind, m5: m5, m15: m15, atr5: atr5, atr15: atr15, asOf: asOf, start: start, end: end, out: &out}
			d.dir, d.sign, d.ob, d.ob15 = market.Buy, 1, m5, m15
			if !buy {
				d.dir, d.sign, d.ob, d.ob15 = market.Sell, -1, market.Mirror(m5), market.Mirror(m15)
			}
			d.level = d.sign * anchor.Low
			if e.edge == EdgeHigh {
				d.level = d.sign * anchor.High
			}
			byEdge[e.edge] = append(byEdge[e.edge], d.episodes()...)
		}
	}

	// One thesis per edge per trading day: the first CONFIRMED episode on an edge (a stop run or an acceptance) owns it. A later one is refused,
	// and the owner is built while it is fresh.
	for _, edge := range []string{EdgeHigh, EdgeLow} {
		list := byEdge[edge]
		sort.SliceStable(list, func(i, j int) bool {
			if list[i].confirm != list[j].confirm {
				return list[i].confirm < list[j].confirm
			}
			return list[i].d.kind < list[j].d.kind
		})
		for i, e := range list {
			switch {
			case i > 0:
				e.d.reject(ReasonEdgeLocked, e.confirm)
			case e.d.barsBetween(e.confirm, asOf) > cfg.ConfirmationMaxAgeBars:
				// aged out: nothing else may take the edge
			default:
				var setup *Setup
				var reason string
				if e.d.kind == KindSweepReclaim {
					setup, reason = e.d.finalizeSweep(e)
				} else {
					setup, reason = e.d.finalizeRetest(e)
				}
				if reason != "" {
					e.d.reject(reason, e.confirm)
				} else {
					out.Setups = append(out.Setups, e.d.toOriginal(*setup))
				}
			}
		}
	}
	sort.SliceStable(out.Setups, func(i, j int) bool {
		a, b := out.Setups[i], out.Setups[j]
		if a.ConfirmedAt != b.ConfirmedAt {
			return a.ConfirmedAt > b.ConfirmedAt
		}
		return a.Edge < b.Edge
	})
	return out
}

// detector evaluates ONE edge and setup kind in the bullish orientation (the edge is support, the trade a BUY); a SELL runs on mirrored candles.
type detector struct {
	cfg        Config
	in         Input
	anchor     Anchor
	edge, kind string
	dir        market.Direction
	sign       float64
	level      float64
	m5, ob     []market.Candle
	m15, ob15  []market.Candle
	atr5       *indicator.WilderWindow
	atr15      *indicator.WilderWindow
	asOf       int
	start, end int
	out        *Analysis
}

type episode struct {
	d       *detector
	confirm int
	sweep   *strategyutil.LevelEpisode
	retest  *strategyutil.LevelRetest
}

func (d *detector) sweepParams() strategyutil.LevelSweepParams {
	c := d.cfg
	return strategyutil.LevelSweepParams{
		PipSize: c.PipSize, MinSweepPips: c.MinimumSweepPips, MinSweepATR: c.MinimumSweepATR, MinReclaimPips: c.MinimumReclaimPips, MinReclaimATR: c.MinimumReclaimATR,
		ReclaimMaxBars: c.ReclaimMaxBars, PivotBars: c.StructurePivotBars, LookbackBars: c.StructureLookbackBars, MSSMaxBars: c.MSSMaxBars,
		MinMSSBody: c.MinimumMSSBodyRatio, MinMSSDisplacement: c.MinimumMSSDisplacementATR, MinMSSCloseStrength: c.MinimumMSSCloseStrength,
		ConfirmationMaxAge: 1 << 30, MinWickRatio: c.MinimumWickRatio,
	}
}

func (d *detector) retestParams() strategyutil.LevelRetestParams {
	c := d.cfg
	half := c.EntryBandPips * c.PipSize / 2
	return strategyutil.LevelRetestParams{PipSize: c.PipSize, AcceptanceCloses: c.AcceptanceCloses, RetestMaxBars: c.RetestMaxBars, BandBelow: half, BandAbove: half, MinWickRatio: c.MinimumWickRatio}
}

func (d *detector) reject(reason string, at int) {
	r := Rejection{Reason: reason, Kind: d.kind, Direction: d.dir, Edge: d.edge}
	if at >= 0 && at < len(d.m5) {
		r.At = d.m5[at].Time
	}
	d.out.Rejections = append(d.out.Rejections, r)
}

func (d *detector) barsBetween(from, to int) int {
	return int((d.m5[to].Time - d.m5[from].Time) / m5Seconds)
}

// episodes follows every stop run (or every accepted break) of the edge that started inside the active window.
func (d *detector) episodes() []episode {
	var out []episode
	if d.kind == KindSweepReclaim {
		sweeps, rejections := strategyutil.FindLevelSweeps(d.ob, d.atr5, d.level, d.start, d.end, d.sweepParams())
		for i := range sweeps {
			ep := sweeps[i]
			out = append(out, episode{d: d, confirm: ep.Confirm, sweep: &ep})
		}
		for _, r := range rejections {
			d.reject(r.Reason, r.Sweep)
		}
		return out
	}
	rp := d.retestParams()
	for i := 1; i < len(d.ob15); i++ {
		c := d.ob15[i]
		closeAt := c.Time + m15Seconds
		if closeAt < d.anchor.AvailableAt || closeAt > d.m5[d.end].Time+m5Seconds {
			continue
		}
		atr, ok := d.atr15.At(i)
		if !ok || !(d.ob15[i-1].Close <= d.level && c.Close > d.level+d.cfg.BreakBufferATR*atr) {
			continue
		}
		// The M5 candle that closes at the same instant as the break candle.
		b := sort.Search(len(d.m5), func(k int) bool { return d.m5[k].Time >= closeAt-m5Seconds })
		if b >= len(d.m5) || d.m5[b].Time != closeAt-m5Seconds {
			continue
		}
		r, reason, found := strategyutil.FindLevelRetest(d.ob, d.level, b, rp)
		switch {
		case found:
			rr := r
			out = append(out, episode{d: d, confirm: r.Confirm, retest: &rr})
		case reason != "":
			d.reject(reason, b)
		}
	}
	return out
}

// objective chooses the target for a setup whose entry reference is `entry`: stop-run setups aim at the range mid, then the opposite edge;
// accepted-break setups at the nearest opposing liquidity, then the measured move.
func (d *detector) objective(entry, minDistance float64) (float64, string, bool) {
	width := d.anchor.Width()
	if d.kind == KindSweepReclaim {
		opposite := d.level + width // the other edge, oriented
		if mid := d.level + width/2; mid-entry >= minDistance {
			return mid, TargetRangeMid, true
		}
		if opposite-entry >= minDistance {
			return opposite, TargetOppositeEdge, true
		}
		return 0, "", false
	}
	if d.in.Liquidity != nil {
		if p, ok := d.in.Liquidity(d.dir, d.sign*entry, minDistance); ok {
			if oriented := d.sign * p; oriented-entry >= minDistance-1e-9 {
				return oriented, TargetLiquidity, true
			}
		}
	}
	if move := d.level + d.cfg.MeasuredMoveFraction*width; move-entry >= minDistance {
		return move, TargetMeasuredMove, true
	}
	return 0, "", false
}

func (d *detector) finishGeometry(e episode, lower, upper, extreme float64, setup *Setup) string {
	atrC, ok := d.atr5.At(e.confirm)
	if !ok {
		return strategyutil.LevelReasonATRUnavailable
	}
	pip := d.cfg.PipSize
	for k := e.confirm + 1; k <= d.asOf; k++ {
		if d.ob[k].Close < d.level || d.ob[k].Low < extreme {
			return ReasonInvalidatedAfterConfirm
		}
	}
	if d.ob[d.asOf].Close < lower {
		return ReasonPriceThroughEntry
	}
	stop := extreme - math.Max(d.cfg.StopBufferPips*pip, d.cfg.StopBufferATR*atrC)
	if !(stop < lower) {
		return ReasonInvalidStopGeometry
	}
	risk := upper - stop
	target, kind, found := d.objective(upper, d.cfg.MinimumTargetR*risk)
	if !found || !(target > upper) {
		return ReasonNoTargetRoom
	}
	for k := e.confirm; k <= d.asOf; k++ {
		if d.ob[k].High >= target {
			return ReasonTargetAlreadyReached
		}
	}
	setup.EntryLow, setup.EntryHigh, setup.Stop, setup.Target, setup.TargetKind, setup.M5ATR = lower, upper, stop, target, kind, atrC
	setup.RiskPips, setup.RewardPips, setup.TechnicalRR = risk/pip, (target-upper)/pip, (target-upper)/risk
	return ""
}

func (d *detector) base(confirm int) *Setup {
	return &Setup{Kind: d.kind, Direction: d.dir, Edge: d.edge, Anchor: d.anchor, ConfirmedAt: d.m5[confirm].Time, ConfirmationAge: d.barsBetween(confirm, d.asOf)}
}

// finalizeSweep: the entry band is centred on the reclaimed edge, the stop beyond the manipulation extreme.
func (d *detector) finalizeSweep(e episode) (*Setup, string) {
	ep := e.sweep
	half := d.cfg.EntryBandPips * d.cfg.PipSize / 2
	setup := d.base(e.confirm)
	setup.SweepTime, setup.ReclaimTime = d.m5[ep.Sweep].Time, d.m5[ep.Reclaim].Time
	atrS, _ := d.atr5.At(ep.Sweep)
	setup.SweepDepthATR = ep.Depth / math.Max(atrS, 1e-12)
	setup.WickRejection, setup.Structural = ep.WickRejection, true
	setup.ForceATR, setup.BodyRatio, setup.CloseStrength = ep.DisplacementATR, ep.BodyRatio, ep.CloseStrength
	setup.RejectionWick = d.ob[ep.Sweep].LowerWickRatio()
	if reason := d.finishGeometry(e, d.level-half, d.level+half, d.ob[ep.Extreme].Low, setup); reason != "" {
		return nil, reason
	}
	return setup, ""
}

// finalizeRetest: the entry band is the retest band of the accepted edge, the stop behind the retest candle or the edge, whichever is further.
func (d *detector) finalizeRetest(e episode) (*Setup, string) {
	r := e.retest
	half := d.cfg.EntryBandPips * d.cfg.PipSize / 2
	setup := d.base(e.confirm)
	setup.BreakTime, setup.RetestTime = d.m5[r.Break].Time, d.m5[r.Retest].Time
	setup.WickRejection, setup.Engulfing = r.WickRejection, r.Engulfing
	setup.RejectionWick = d.ob[r.Confirm].LowerWickRatio()
	setup.RetestBars = d.barsBetween(r.AcceptEnd, r.Retest)
	// The break candle is the M15 candle that closed at the same instant as M5 candle r.Break.
	for i := range d.ob15 {
		if d.ob15[i].Time+m15Seconds == d.m5[r.Break].Time+m5Seconds {
			if atr, ok := d.atr15.At(i); ok {
				c := d.ob15[i]
				setup.ForceATR, setup.BodyRatio, setup.CloseStrength = (c.Close-c.Open)/atr, c.BodyRatio(), c.CloseLocation()
			}
			break
		}
	}
	setup.Structural = d.brokeSwing(r.Break)
	extreme := math.Min(r.RetestLow, d.level)
	if reason := d.finishGeometry(e, d.level-half, d.level+half, extreme, setup); reason != "" {
		return nil, reason
	}
	return setup, ""
}

// brokeSwing: the break closed above the most recent M5 swing high that was already confirmable before it.
func (d *detector) brokeSwing(b int) bool {
	n := d.cfg.StructurePivotBars
	pivots := strategyutil.PivotHighs(d.ob[:b], n)
	for k := len(pivots) - 1; k >= 0; k-- {
		if p := pivots[k]; p+n < b {
			return d.ob[b].Close > d.ob[p].High
		}
	}
	return false
}

// toOriginal reflects a setup built in the bullish orientation back to the requested direction.
func (d *detector) toOriginal(s Setup) Setup {
	if d.sign > 0 {
		return s
	}
	s.EntryLow, s.EntryHigh = -s.EntryHigh, -s.EntryLow
	s.Stop, s.Target = -s.Stop, -s.Target
	return s
}
