package supplyv8

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Room and target: what stands between the entry and the target. Everything here reads
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

// closeBelow reports whether a close in bars[from:] went below price.
func closeBelow(bars []market.Candle, from int, price float64) bool {
	for i := maxInt(from, 0); i < len(bars); i++ {
		if bars[i].Close < price {
			return true
		}
	}
	return false
}

// obstacles are the prices a SELL has to get through below reference, nearest first:
//
//	(a) every confirmed swing low on the execution frame and on the structure frame that no execution-frame close has gone below since it was confirmed
//	    (a wick through it and back keeps it alive: that is the rejection);
//	(b) the impulse extreme: the lowest low since the departure leg began, which needs no confirmation (a spike bottom may have one bar after it).
func (v *view) obstacles(in instance, reference float64) []float64 {
	var found []float64
	exec := v.frame.Bars
	n := v.cfg.ObstaclePivotBars
	for _, p := range pivots(exec, n, false) {
		if p.Price < reference && !closeBelow(exec, p.Confirmed+1, p.Price) {
			found = append(found, p.Price)
		}
	}
	if in.Frame != nil {
		sbars := in.Frame.Bars
		minutes, _ := in.Timeframe.Minutes()
		for _, p := range pivots(sbars, n, false) {
			if !(p.Price < reference) || closeBelow(sbars, p.Confirmed+1, p.Price) {
				continue
			}
			known := sbars[p.Confirmed].Time + int64(minutes)*60 // the first instant the pivot is knowable
			from := sort.Search(len(exec), func(i int) bool { return exec[i].Time >= known })
			if !closeBelow(exec, from, p.Price) {
				found = append(found, p.Price)
			}
		}
	}
	if extreme, ok := v.impulse(in); ok && extreme < reference {
		found = append(found, extreme)
	}
	sort.Sort(sort.Reverse(sort.Float64Slice(found)))
	return found
}

// impulse is the extreme of the departure leg that made the zone: the lowest low of the structure frame from the leg's first candle, and of the execution
// frame from the time that candle opened, up to the decision bar.
func (v *view) impulse(in instance) (extreme float64, ok bool) {
	if in.Frame == nil || in.LegStart < 0 || in.LegStart >= len(in.Frame.Bars) {
		return 0, false
	}
	sbars := in.Frame.Bars
	extreme = math.Inf(1)
	for _, b := range sbars[in.LegStart:] {
		extreme = math.Min(extreme, b.Low)
	}
	for _, b := range v.frame.Bars {
		if b.Time >= sbars[in.LegStart].Time {
			extreme = math.Min(extreme, b.Low)
		}
	}
	return extreme, !math.IsInf(extreme, 0)
}

// roomResult is the room judgement of one entry.
type roomResult struct {
	Target  float64
	Label   string
	RoomR   float64 // distance to the target in units of the risk
	Refused bool
}

// room places the target and judges the entry. risk is |reference - invalidation|. The target is the nearest obstacle when it is at least
// MinimumTargetR x risk away (otherwise the entry has no room: no_target_room) and a fixed FallbackTargetR x risk when nothing stands in the way. A zone
// sits at the origin of its departure, so there is no extension rule.
func (v *view) room(in instance, reference, risk float64) roomResult {
	var out roomResult
	if list := v.obstacles(in, reference); len(list) > 0 {
		nearest := list[0]
		out.Target, out.Label = nearest, "nearest_opposing_structure"
		out.Refused = math.Abs(nearest-reference) < v.cfg.MinimumTargetR*risk
	} else {
		out.Target, out.Label = reference-v.cfg.FallbackTargetR*risk, fixedRewardRisk
	}
	if risk > 0 {
		out.RoomR = math.Abs(out.Target-reference) / risk
	}
	return out
}
