package fvg

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Room, target and extension: what stands between the entry and the target, and how stretched the leg is that the gap sits in. Everything here reads
// closed bars at or before the decision bar and nothing after (a pivot needs its right-hand bars closed).

// pivot is a fractal swing: a high (or low) that every one of the n bars before it beat and that no one of the n bars after it beat.
type pivot struct {
	Index     int
	Price     float64
	Confirmed int // the index of the n-th bar after it, the first bar by which it is known
}

// pivots returns the confirmed swing highs (highs=true) or lows of bars: only pivots whose right-hand bars are all closed within bars.
func pivots(bars []market.Candle, n int, highs bool) []pivot {
	var out []pivot
	for i := n; i < len(bars)-n; i++ {
		price := bars[i].Low
		if highs {
			price = bars[i].High
		}
		ok := true
		for j := i - n; j < i && ok; j++ { // strictly beaten on the left
			if highs {
				ok = bars[j].High < price
			} else {
				ok = bars[j].Low > price
			}
		}
		for j := i + 1; j <= i+n && ok; j++ { // not beaten on the right
			if highs {
				ok = bars[j].High <= price
			} else {
				ok = bars[j].Low >= price
			}
		}
		if ok {
			out = append(out, pivot{Index: i, Price: price, Confirmed: i + n})
		}
	}
	return out
}

func beyond(direction market.Direction, price, reference float64) bool {
	if direction == market.Buy {
		return price > reference
	}
	return price < reference
}

// closeBeyond reports whether a close in bars[from:] went through price in the trade direction (above for a BUY's high, below for a SELL's low).
func closeBeyond(direction market.Direction, bars []market.Candle, from int, price float64) bool {
	for i := maxInt(from, 0); i < len(bars); i++ {
		if direction == market.Buy && bars[i].Close > price || direction == market.Sell && bars[i].Close < price {
			return true
		}
	}
	return false
}

// obstacles are the prices a BUY has to get through above reference (a SELL below it), nearest first:
//
//	(a) every confirmed swing high on the execution frame and on the structure frame that no execution-frame close has gone through since it was
//	    confirmed (a wick through it and back keeps it alive: that is the rejection);
//	(b) the impulse extreme: the highest high since the gap's first candle, which needs no confirmation (a spike top may have one bar after it).
func (v *view) obstacles(in instance, reference float64) []float64 {
	var found []float64
	exec := v.frame.Bars
	n := v.cfg.ObstaclePivotBars
	for _, p := range pivots(exec, n, v.dir == market.Buy) {
		if beyond(v.dir, p.Price, reference) && !closeBeyond(v.dir, exec, p.Confirmed+1, p.Price) {
			found = append(found, p.Price)
		}
	}
	if in.Frame != nil {
		sbars := in.Frame.Bars
		minutes, _ := in.Timeframe.Minutes()
		for _, p := range pivots(sbars, n, v.dir == market.Buy) {
			if !beyond(v.dir, p.Price, reference) || closeBeyond(v.dir, sbars, p.Confirmed+1, p.Price) {
				continue
			}
			known := sbars[p.Confirmed].Time + int64(minutes)*60 // the first instant the pivot is knowable
			from := sort.Search(len(exec), func(i int) bool { return exec[i].Time >= known })
			if !closeBeyond(v.dir, exec, from, p.Price) {
				found = append(found, p.Price)
			}
		}
	}
	if extreme, _, ok := v.impulse(in); ok && beyond(v.dir, extreme, reference) {
		found = append(found, extreme)
	}
	sort.Float64s(found)
	if v.dir == market.Sell {
		for i, j := 0, len(found)-1; i < j; i, j = i+1, j-1 {
			found[i], found[j] = found[j], found[i]
		}
	}
	return found
}

// impulse is the leg the gap sits in: its extreme (the highest high for a BUY since the gap's first candle, to the decision bar, over the structure frame
// and the execution frame) and its origin (the lowest low of the structure frame from LegLookbackBars before the gap's first candle to that candle).
func (v *view) impulse(in instance) (extreme, origin float64, ok bool) {
	if in.Frame == nil {
		return 0, 0, false
	}
	sbars := in.Frame.Bars
	first := in.FrameOrigin - 2
	if first < 0 || first >= len(sbars) {
		return 0, 0, false
	}
	buy := v.dir == market.Buy
	extreme, origin = math.Inf(1), math.Inf(-1)
	if buy {
		extreme, origin = math.Inf(-1), math.Inf(1)
	}
	for _, b := range sbars[first:] {
		if buy {
			extreme = math.Max(extreme, b.High)
		} else {
			extreme = math.Min(extreme, b.Low)
		}
	}
	for _, b := range v.frame.Bars {
		if b.Time < sbars[first].Time {
			continue
		}
		if buy {
			extreme = math.Max(extreme, b.High)
		} else {
			extreme = math.Min(extreme, b.Low)
		}
	}
	for _, b := range sbars[maxInt(0, first-v.cfg.LegLookbackBars) : first+1] {
		if buy {
			origin = math.Min(origin, b.Low)
		} else {
			origin = math.Max(origin, b.High)
		}
	}
	return extreme, origin, !math.IsInf(extreme, 0) && !math.IsInf(origin, 0)
}

// roomResult is the room and extension judgement of one entry.
type roomResult struct {
	Target      float64
	Label       string
	RoomR       float64 // distance to the target in units of the risk
	Retracement float64 // how far the entry sits back from the leg's extreme, as a share of the leg (NaN without a leg)
	Reasons     []string
}

// room places the target and judges the entry. risk is |reference - invalidation|. The target is the nearest obstacle when it is at least
// MinimumTargetR x risk away (otherwise the entry has no room: no_target_room) and a fixed FallbackTargetR x risk when nothing stands in the way. A leg
// that is both stretched (ExtensionMinimumLegATR of the structure frame's ATR) and barely retraced (under MinimumLegRetracement) refuses
// entry_at_leg_extreme. Both reasons are reported when both apply.
func (v *view) room(in instance, reference, risk float64) roomResult {
	out := roomResult{Retracement: math.NaN()}
	sign := 1.0
	if v.dir == market.Sell {
		sign = -1
	}
	if list := v.obstacles(in, reference); len(list) > 0 {
		nearest := list[0]
		out.Target, out.Label = nearest, "nearest_opposing_structure"
		if math.Abs(nearest-reference) < v.cfg.MinimumTargetR*risk {
			out.Reasons = append(out.Reasons, refusedNoTargetRoom)
		}
	} else {
		out.Target, out.Label = reference+sign*v.cfg.FallbackTargetR*risk, fixedRewardRisk
	}
	if risk > 0 {
		out.RoomR = math.Abs(out.Target-reference) / risk
	}
	if extreme, origin, ok := v.impulse(in); ok {
		leg := math.Abs(extreme - origin)
		atr := frameATRAt(in)
		if leg > 0 {
			out.Retracement = math.Abs(extreme-reference) / leg
			if atr > 0 && leg >= v.cfg.ExtensionMinimumLegATR*atr && out.Retracement < v.cfg.MinimumLegRetracement {
				out.Reasons = append(out.Reasons, refusedEntryAtLegExtreme)
			}
		}
	}
	return out
}

// confirmedThroughRecentSwing reports whether the confirming bar closed through the most recent execution-frame swing in the trade direction that was
// already confirmed before it (a measured structural agreement).
func (v *view) confirmedThroughRecentSwing(c int) bool {
	bars := v.frame.Bars
	var recent *pivot
	list := pivots(bars, v.cfg.ObstaclePivotBars, v.dir == market.Buy)
	for k := range list {
		if list[k].Confirmed < c {
			recent = &list[k]
		}
	}
	if recent == nil {
		return false
	}
	if v.dir == market.Buy {
		return bars[c].Close > recent.Price
	}
	return bars[c].Close < recent.Price
}

// frameATRAt is the gap's frame ATR as of the candle that completed the gap.
func frameATRAt(in instance) float64 {
	return atrAt(in.Frame.ATR, in.Frame.Bars, in.FrameOrigin, medianATR(in.Frame.ATR))
}

// middleBodyATR is the body of the gap's middle candle in the ATR of its own frame (0 when it cannot be read).
func (v *view) middleBodyATR(in instance) float64 {
	if in.Frame == nil || in.FrameOrigin < 2 || in.FrameOrigin >= len(in.Frame.Bars) {
		return 0
	}
	atr := frameATRAt(in)
	if !(atr > 0) {
		return 0
	}
	middle := in.Frame.Bars[in.FrameOrigin-1]
	return math.Abs(middle.Close-middle.Open) / atr
}

// steepness is how fast the gap formed: |third close - first open| over the three candles of the gap, per candle, in the ATR of its own frame
// (informational; never a gate).
func (v *view) steepness(in instance) float64 {
	if in.Frame == nil || in.FrameOrigin < 2 || in.FrameOrigin >= len(in.Frame.Bars) {
		return math.NaN()
	}
	atr := frameATRAt(in)
	if !(atr > 0) {
		return math.NaN()
	}
	return math.Abs(in.Frame.Bars[in.FrameOrigin].Close-in.Frame.Bars[in.FrameOrigin-2].Open) / 3 / atr
}
