package roundnumber

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const m5Seconds = int64(300)

// Input is the closed-candle data one evaluation reads, oldest first, plus the engine's liquidity read.
type Input struct {
	M5 []market.Candle
	// Liquidity is the nearest unswept objective in the trade direction at least minDistance from `from` (prices in the market's own orientation),
	// as the engine's pools have it. Nil means none.
	Liquidity func(direction market.Direction, from, minDistance float64) (price float64, ok bool)
}

// Detect evaluates both Round Number setups as of the last candle: for each handle near price, a stop run through it that was taken back, and an
// accepted break of it that was retested.
func Detect(cfg Config, in Input) Analysis { return DetectAsOf(cfg, in, len(in.M5)-1) }

// DetectAsOf is Detect as of M5 index asOf: candles after it are never read. The prefix-invariance tests rely on it: the result with the full
// series and asOf=k equals the result with series[:k+1].
func DetectAsOf(cfg Config, in Input, asOf int) Analysis {
	var out Analysis
	if asOf < 0 || asOf >= len(in.M5) {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInvalidCandles})
		return out
	}
	m5 := in.M5[:asOf+1]
	out.EvaluatedAt = m5[asOf].Time + m5Seconds
	if !market.ValidSeries(m5) {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInvalidCandles})
		return out
	}
	atr := indicator.NewWilderWindow(m5, cfg.ATRLength, cfg.ATRWindowBars)
	atrNow, ok := atr.At(asOf)
	if !atr.Enough() || !ok {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInsufficientM5History})
		return out
	}
	day := strategyutil.TradingDay(m5[asOf].Time, cfg.TradingDayRolloverHour)
	dayStart := asOf
	for dayStart > 0 && strategyutil.TradingDay(m5[dayStart-1].Time, cfg.TradingDayRolloverHour) == day {
		dayStart--
	}
	horizon := cfg.ReclaimMaxBars + cfg.MSSMaxBars + cfg.AcceptanceCloses + cfg.RetestMaxBars + cfg.ConfirmationMaxAgeBars + 2
	from := dayStart - horizon
	if from < 1 {
		from = 1
	}

	last := m5[asOf].Close
	reach := cfg.MaxHandleDistanceATR * atrNow
	for k := math.Floor((last - reach) / cfg.HandleStep); k*cfg.HandleStep <= last+reach; k++ {
		handle := k * cfg.HandleStep
		if math.Abs(handle-last) > reach {
			continue
		}
		out.Handles++
		for _, dir := range []market.Direction{market.Buy, market.Sell} {
			d := &detector{cfg: cfg, in: in, dir: dir, sign: 1, handle: handle, m5: m5, ob: m5, atr: atr, asOf: asOf, day: day, dayStart: dayStart, from: from, out: &out}
			if dir == market.Sell {
				d.sign, d.ob = -1, market.Mirror(m5)
			}
			d.level = d.sign * handle
			d.scan()
		}
	}
	sort.SliceStable(out.Setups, func(i, j int) bool {
		a, b := out.Setups[i], out.Setups[j]
		if a.ConfirmedAt != b.ConfirmedAt {
			return a.ConfirmedAt > b.ConfirmedAt
		}
		if a.Handle != b.Handle {
			return a.Handle < b.Handle
		}
		return a.Direction < b.Direction
	})
	return out
}

// detector evaluates ONE handle and direction in the bullish orientation (the handle is support, the trade a BUY). For a SELL the candles are
// mirrored, so every comparison reads as the bullish case and the setup is reflected back on the way out.
type detector struct {
	cfg    Config
	in     Input
	dir    market.Direction
	sign   float64
	handle float64
	level  float64
	m5     []market.Candle // as given
	ob     []market.Candle // oriented
	atr    *indicator.WilderWindow
	asOf   int
	day    int64
	// dayStart is the first candle of the current trading day; from the first candle a sweep or break may start from.
	dayStart, from int
	out            *Analysis
}

type episode struct {
	kind    string
	confirm int
	sweep   *strategyutil.LevelEpisode
	retest  *strategyutil.LevelRetest
	brk     int
}

func (d *detector) sweepParams() strategyutil.LevelSweepParams {
	c := d.cfg
	return strategyutil.LevelSweepParams{
		PipSize: c.PipSize, MinSweepPips: c.MinimumSweepPips, MaxSweepPips: c.MaximumSweepPips, MinReclaimPips: c.MinimumReclaimPips,
		ReclaimMaxBars: c.ReclaimMaxBars, AcceptanceCloses: c.AcceptanceCloses, PivotBars: c.StructurePivotBars, LookbackBars: c.StructureLookbackBars,
		MSSMaxBars: c.MSSMaxBars, MinMSSBody: c.MinimumMSSBodyRatio, MinMSSDisplacement: c.MinimumMSSDisplacementATR, MinMSSCloseStrength: c.MinimumMSSCloseStrength,
		ConfirmationMaxAge: 1 << 30, MinWickRatio: c.MinimumWickRatio, RequireOrigin: true,
	}
}

func (d *detector) retestParams() strategyutil.LevelRetestParams {
	c := d.cfg
	return strategyutil.LevelRetestParams{
		PipSize: c.PipSize, AcceptanceCloses: c.AcceptanceCloses, RetestMaxBars: c.RetestMaxBars,
		BandBelow: c.RetestTolerancePips * c.PipSize, BandAbove: c.EntryBandPips * c.PipSize, MinWickRatio: c.MinimumWickRatio,
	}
}

func (d *detector) reject(reason, kind string, at int) {
	r := Rejection{Reason: reason, Kind: kind, Direction: d.dir, Handle: d.handle}
	if at >= 0 && at < len(d.m5) {
		r.At = d.m5[at].Time
	}
	d.out.Rejections = append(d.out.Rejections, r)
}

func (d *detector) barsBetween(from, to int) int {
	return int((d.m5[to].Time - d.m5[from].Time) / m5Seconds)
}

func (d *detector) scan() {
	var episodes []episode
	sweeps, rejections := strategyutil.FindLevelSweeps(d.ob, d.atr, d.level, d.from, d.asOf, d.sweepParams())
	for i := range sweeps {
		ep := sweeps[i]
		episodes = append(episodes, episode{kind: KindSweepReclaim, confirm: ep.Confirm, sweep: &ep})
	}
	for _, r := range rejections {
		if r.Sweep >= d.dayStart {
			d.reject(r.Reason, KindSweepReclaim, r.Sweep)
		}
	}
	rp := d.retestParams()
	for b := d.from; b <= d.asOf; b++ {
		if !d.isBreak(b) {
			continue
		}
		r, reason, ok := strategyutil.FindLevelRetest(d.ob, d.level, b, rp)
		switch {
		case ok:
			rr := r
			episodes = append(episodes, episode{kind: KindBreakRetest, confirm: r.Confirm, retest: &rr, brk: b})
		case reason != "" && b >= d.dayStart:
			d.reject(reason, KindBreakRetest, b)
		}
	}
	// One episode per handle per side per trading day: the first CONFIRMED one owns it (a thesis the stop budget later refuses is still that
	// handle's thesis for the day). A later confirmation is refused, the owner is built while it is fresh.
	var today []episode
	for _, e := range episodes {
		if strategyutil.TradingDay(d.m5[e.confirm].Time, d.cfg.TradingDayRolloverHour) == d.day {
			today = append(today, e)
		}
	}
	sort.SliceStable(today, func(i, j int) bool {
		if today[i].confirm != today[j].confirm {
			return today[i].confirm < today[j].confirm
		}
		return today[i].kind < today[j].kind
	})
	for i, e := range today {
		if i > 0 {
			d.reject(ReasonHandleLocked, e.kind, e.confirm)
			continue
		}
		if d.barsBetween(e.confirm, d.asOf) > d.cfg.ConfirmationMaxAgeBars {
			continue // the owner has aged out; nothing else may take the handle today
		}
		var setup *Setup
		var reason string
		if e.kind == KindSweepReclaim {
			setup, reason = d.finalizeSweep(e)
		} else {
			setup, reason = d.finalizeRetest(e)
		}
		if reason != "" {
			d.reject(reason, e.kind, e.confirm)
			continue
		}
		d.out.Setups = append(d.out.Setups, d.toOriginal(*setup))
	}
}

// isBreak is a candle that closes through the handle (bullish: above it) by the buffer, with body and force, from a prior close on the origin side.
func (d *detector) isBreak(b int) bool {
	if b < 1 {
		return false
	}
	c := d.ob[b]
	atrBefore, ok := d.atr.At(b - 1)
	if !ok {
		return false
	}
	return d.ob[b-1].Close <= d.level && c.Close > d.level+d.cfg.BreakBufferPips*d.cfg.PipSize && c.Close > c.Open &&
		c.BodyRatio() >= d.cfg.MinimumBreakBodyRatio && c.Range() >= d.cfg.MinimumBreakRangeATR*atrBefore
}

// objective is the target: the nearest unswept liquidity at least minDistance from the entry, else the next round handle that far.
func (d *detector) objective(entry, minDistance float64) (float64, string, bool) {
	if d.in.Liquidity != nil {
		if p, ok := d.in.Liquidity(d.dir, d.sign*entry, minDistance); ok {
			if oriented := d.sign * p; oriented > entry+minDistance-1e-9 {
				return oriented, TargetLiquidity, true
			}
		}
	}
	step := d.cfg.HandleStep
	next := math.Ceil((entry+minDistance)/step-1e-9) * step
	if next > entry && next-entry >= minDistance-1e-9 {
		return next, TargetNextHandle, true
	}
	return 0, "", false
}

// finishGeometry turns the structural extreme into the stop, applies the thesis-still-alive checks since the confirmation and chooses the target.
func (d *detector) finishGeometry(e episode, lower, upper, extreme, stopFloor float64, setup *Setup) string {
	atrC, ok := d.atr.At(e.confirm)
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
	stop := math.Min(extreme, stopFloor) - math.Max(d.cfg.StopBufferPips*pip, d.cfg.StopBufferATR*atrC)
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

func (d *detector) base(kind string, confirm int) *Setup {
	return &Setup{
		Kind: kind, Direction: d.dir, Handle: d.handle, Major: math.Mod(d.handle, d.cfg.MajorStep) < 1e-6 || d.cfg.MajorStep-math.Mod(d.handle, d.cfg.MajorStep) < 1e-6,
		Day: d.day, ConfirmedAt: d.m5[confirm].Time, ConfirmationAge: d.barsBetween(confirm, d.asOf),
	}
}

// finalizeSweep builds the stop-run setup: the entry is the retest of the reclaimed handle, the stop beyond the manipulation extreme.
func (d *detector) finalizeSweep(e episode) (*Setup, string) {
	ep := e.sweep
	pip := d.cfg.PipSize
	setup := d.base(KindSweepReclaim, e.confirm)
	setup.SweepTime, setup.ReclaimTime = d.m5[ep.Sweep].Time, d.m5[ep.Reclaim].Time
	atrS, _ := d.atr.At(ep.Sweep)
	setup.SweepDepthATR = ep.Depth / math.Max(atrS, 1e-12)
	setup.WickRejection, setup.Structural = ep.WickRejection, true
	setup.ForceATR, setup.BodyRatio, setup.CloseStrength = ep.DisplacementATR, ep.BodyRatio, ep.CloseStrength
	setup.RejectionWick = d.ob[ep.Sweep].LowerWickRatio()
	reason := d.finishGeometry(e, d.level, d.level+d.cfg.EntryBandPips*pip, d.ob[ep.Extreme].Low, math.Inf(1), setup)
	if reason != "" {
		return nil, reason
	}
	return setup, ""
}

// finalizeRetest builds the accepted-break setup: the entry is the retest band, the stop beyond the retest candle and the handle.
func (d *detector) finalizeRetest(e episode) (*Setup, string) {
	r := e.retest
	pip := d.cfg.PipSize
	setup := d.base(KindBreakRetest, e.confirm)
	setup.BreakTime, setup.RetestTime = d.m5[r.Break].Time, d.m5[r.Retest].Time
	atrB, _ := d.atr.At(r.Break - 1)
	brk := d.ob[r.Break]
	setup.ForceATR, setup.BodyRatio, setup.CloseStrength = (brk.Close-brk.Open)/math.Max(atrB, 1e-12), brk.BodyRatio(), brk.CloseLocation()
	setup.WickRejection, setup.Engulfing = r.WickRejection, r.Engulfing
	setup.RejectionWick = d.ob[r.Confirm].LowerWickRatio()
	setup.RetestBars = d.barsBetween(r.AcceptEnd, r.Retest)
	setup.Structural = d.brokeSwing(r.Break)
	lower := d.level - d.cfg.RetestTolerancePips*pip
	upper := d.level + d.cfg.EntryBandPips*pip
	reason := d.finishGeometry(e, lower, upper, r.RetestLow, d.level-d.cfg.StopBufferPips*pip, setup)
	if reason != "" {
		return nil, reason
	}
	return setup, ""
}

// brokeSwing: the break candle closed above the most recent swing high that was already confirmable before it.
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

// toOriginal reflects a setup built in the bullish orientation back to the requested direction. Magnitudes are reflection-invariant; absolute
// prices are not.
func (d *detector) toOriginal(s Setup) Setup {
	if d.sign > 0 {
		return s
	}
	s.EntryLow, s.EntryHigh = -s.EntryHigh, -s.EntryLow
	s.Stop, s.Target = -s.Stop, -s.Target
	return s
}
