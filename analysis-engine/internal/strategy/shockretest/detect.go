package shockretest

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

const m5Seconds = int64(300)

// Input is the closed-candle data one evaluation reads, oldest first.
type Input struct{ M5 []market.Candle }

// Detect evaluates the Shock Retest thesis as of the last candle: a volatility shock, a settle, the first pullback into the 50 to 61.8 percent
// retracement of the leg, rejected in the shock direction.
func Detect(cfg Config, in Input) Analysis { return DetectAsOf(cfg, in, len(in.M5)-1) }

// DetectAsOf is Detect as of index asOf: candles after it are never read.
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
	if !atr.Enough() {
		out.Rejections = append(out.Rejections, Rejection{Reason: ReasonInsufficientM5History})
		return out
	}
	mirrored := market.Mirror(m5)
	up, down := shocks(cfg, m5, atr), shocks(cfg, mirrored, atr)
	out.Shocks = len(up) + len(down)
	for _, side := range []struct {
		dir          market.Direction
		sign         float64
		bars         []market.Candle
		own, counter []shock
	}{{market.Buy, 1, m5, up, down}, {market.Sell, -1, mirrored, down, up}} {
		d := &detector{cfg: cfg, dir: side.dir, sign: side.sign, m5: m5, ob: side.bars, atr: atr, asOf: asOf, out: &out}
		d.run(side.own, side.counter)
	}
	sort.SliceStable(out.Setups, func(i, j int) bool {
		if out.Setups[i].ConfirmedAt != out.Setups[j].ConfirmedAt {
			return out.Setups[i].ConfirmedAt > out.Setups[j].ConfirmedAt
		}
		return out.Setups[i].Direction < out.Setups[j].Direction
	})
	return out
}

// shock is a volatility shock in the bullish orientation: a single wide, decisive candle, or two consecutive bullish candles whose combined leg is
// wide. start is the first candle of the leg (its open is the origin), end the candle that completes it.
type shock struct{ start, end int }

// shocks lists the shocks of an oriented series. The ATR yardstick is measured on the window BEFORE the shock, so a shock never inflates its own
// threshold. Mirroring leaves true ranges unchanged, so one ATR serves both orientations.
func shocks(cfg Config, bars []market.Candle, atr *indicator.WilderWindow) []shock {
	var out []shock
	for i := 1; i < len(bars); i++ {
		c := bars[i]
		if !(c.Close > c.Open) {
			continue
		}
		if i >= 2 && bars[i-1].Close > bars[i-1].Open {
			if before, ok := atr.At(i - 2); ok && bars[i].Close-bars[i-1].Open >= cfg.ShockLegATR*before {
				out = append(out, shock{start: i - 1, end: i})
				continue
			}
		}
		if before, ok := atr.At(i - 1); ok && c.Range() >= cfg.ShockRangeATR*before && c.BodyRatio() >= cfg.MinimumShockBodyRatio {
			out = append(out, shock{start: i, end: i})
		}
	}
	return out
}

type detector struct {
	cfg    Config
	dir    market.Direction
	sign   float64
	m5, ob []market.Candle
	atr    *indicator.WilderWindow
	asOf   int
	out    *Analysis
}

func (d *detector) reject(reason string, at int) {
	r := Rejection{Reason: reason, Direction: d.dir}
	if at >= 0 && at < len(d.m5) {
		r.At = d.m5[at].Time
	}
	d.out.Rejections = append(d.out.Rejections, r)
}

func (d *detector) barsBetween(from, to int) int {
	return int((d.m5[to].Time - d.m5[from].Time) / m5Seconds)
}

// run follows the episodes of this orientation. A shock that continues an earlier one before its pullback extends the same leg (the origin stays
// the first candle's open, the settle restarts); a shock of the opposite direction before the confirmation ends the episode.
func (d *detector) run(own, counter []shock) {
	counterEnds := map[int]bool{}
	for _, s := range counter {
		counterEnds[s.end] = true
	}
	ownByEnd := map[int]shock{}
	for _, s := range own {
		ownByEnd[s.end] = s
	}
	consumed := -1
	for _, sh := range own {
		if sh.end <= consumed {
			continue
		}
		consumed = d.episode(sh, ownByEnd, counterEnds)
	}
}

// episode runs one shock to its outcome and returns the last candle index it used.
func (d *detector) episode(sh shock, ownByEnd map[int]shock, counterEnds map[int]bool) int {
	cfg := d.cfg
	origin, extreme := d.ob[sh.start].Open, d.ob[sh.start].High
	for k := sh.start; k <= sh.end; k++ {
		extreme = math.Max(extreme, d.ob[k].High)
	}
	legBefore, _ := d.atr.At(sh.start - 1)
	shockEnd := sh.end
	settleEnd := shockEnd + cfg.SettleBars
	first := -1
	retestLow := math.Inf(1)
	for j := shockEnd + 1; j <= d.asOf; j++ {
		if counterEnds[j] {
			d.reject(ReasonCounterShock, j)
			return j
		}
		c := d.ob[j]
		if next, isShock := ownByEnd[j]; isShock && first < 0 {
			// The leg continues: it is one move, not two.
			for k := next.start; k <= next.end; k++ {
				extreme = math.Max(extreme, d.ob[k].High)
			}
			shockEnd, settleEnd = j, j+cfg.SettleBars
			continue
		}
		if j <= settleEnd {
			extreme = math.Max(extreme, c.High)
			continue
		}
		leg := extreme - origin
		bandHigh, bandLow := extreme-RetracementNear*leg, extreme-RetracementFar*leg
		if d.barsBetween(shockEnd, j)-cfg.SettleBars > cfg.RetestMaxBars && first < 0 {
			d.reject(ReasonNoRetest, shockEnd)
			return j
		}
		if c.Close < extreme-RetracementDead*leg {
			d.reject(ReasonRetestTooDeep, j)
			return j
		}
		if first < 0 {
			if c.Low > bandHigh {
				extreme = math.Max(extreme, c.High) // no pullback yet: the leg is still extending
				continue
			}
			first = j
		}
		retestLow = math.Min(retestLow, c.Low)
		wick := c.Close > c.Open && c.LowerWickRatio() >= cfg.MinimumWickRatio
		engulf := j > 0 && d.ob[j-1].Close < d.ob[j-1].Open && c.Close > c.Open && c.Close >= d.ob[j-1].Open && c.Open <= d.ob[j-1].Close
		if wick || engulf {
			d.confirm(confirmed{start: sh.start, shockEnd: shockEnd, origin: origin, extreme: extreme, atrBefore: legBefore, first: first, at: j, bandLow: bandLow, bandHigh: bandHigh, retestLow: retestLow, wick: wick, engulf: engulf})
			return j
		}
		if j > first {
			d.reject(ReasonRetestNotRejected, j)
			return j
		}
	}
	if first < 0 && d.barsBetween(shockEnd, d.asOf)-cfg.SettleBars > cfg.RetestMaxBars {
		d.reject(ReasonNoRetest, shockEnd)
	}
	return d.asOf
}

// confirmed is a pullback that rejected: the leg, the band, the retest and the rejecting candle.
type confirmed struct {
	start, shockEnd, first, at                               int
	origin, extreme, atrBefore, bandLow, bandHigh, retestLow float64
	wick, engulf                                             bool
}

// confirm builds the setup of a confirmed retest if it is fresh, and refuses it with its exact reason when a rule does.
func (d *detector) confirm(c confirmed) {
	if d.barsBetween(c.at, d.asOf) > d.cfg.ConfirmationMaxAgeBars {
		return // aged out: the confirmation is not a live observation any more
	}
	atrC, ok := d.atr.At(c.at)
	if !ok {
		d.reject(ReasonATRUnavailable, c.at)
		return
	}
	pip := d.cfg.PipSize
	floor := math.Min(c.retestLow, c.origin)
	// Nothing since the confirmation may have invalidated the thesis: a close below the origin, or ANY trade through the swing the stop sits behind.
	for k := c.at + 1; k <= d.asOf; k++ {
		if d.ob[k].Low < floor || d.ob[k].Close < c.origin {
			d.reject(ReasonInvalidatedAfterConfirm, c.at)
			return
		}
	}
	if d.ob[d.asOf].Close < c.bandLow {
		d.reject(ReasonPriceThroughEntry, c.at)
		return
	}
	stop := floor - math.Max(d.cfg.StopBufferPips*pip, d.cfg.StopBufferATR*atrC)
	if !(stop < c.bandLow) {
		d.reject(ReasonInvalidStopGeometry, c.at)
		return
	}
	risk, reward := c.bandHigh-stop, c.extreme-c.bandHigh
	if !(reward > 0) || reward < d.cfg.MinimumTargetR*risk {
		d.reject(ReasonNoTargetRoom, c.at)
		return
	}
	for k := c.at; k <= d.asOf; k++ {
		if d.ob[k].High >= c.extreme {
			d.reject(ReasonTargetAlreadyReached, c.at)
			return
		}
	}
	leg := c.extreme - c.origin
	setup := Setup{
		Direction: d.dir, OriginTime: d.m5[c.start].Time, ShockTime: d.m5[c.shockEnd].Time, Origin: c.origin, Extreme: c.extreme,
		RetestTime: d.m5[c.first].Time, ConfirmedAt: d.m5[c.at].Time, ConfirmationAge: d.barsBetween(c.at, d.asOf),
		EntryLow: c.bandLow, EntryHigh: c.bandHigh, Stop: stop, Target: c.extreme, M5ATR: atrC,
		RiskPips: risk / pip, RewardPips: reward / pip, TechnicalRR: reward / risk,
		WickRejection: c.wick, Engulfing: c.engulf && !c.wick, RejectionWick: d.ob[c.at].LowerWickRatio(),
		Retracement: (c.extreme - c.retestLow) / leg,
	}
	if c.atrBefore > 0 {
		setup.LegATR = leg / c.atrBefore
	}
	d.out.Setups = append(d.out.Setups, d.toOriginal(setup))
}

// toOriginal reflects a setup built in the bullish orientation back to the requested direction.
func (d *detector) toOriginal(s Setup) Setup {
	if d.sign > 0 {
		return s
	}
	s.EntryLow, s.EntryHigh = -s.EntryHigh, -s.EntryLow
	s.Stop, s.Target = -s.Stop, -s.Target
	s.Origin, s.Extreme = -s.Origin, -s.Extreme
	return s
}
