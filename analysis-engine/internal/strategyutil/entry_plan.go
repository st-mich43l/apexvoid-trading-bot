package strategyutil

import (
	"math"
	"sort"
)

// AE-owned entry for a supply or demand zone (SD-5).
//
// The proximal edge of a zone is not always the best technical entry. PlanEntry evaluates a fixed, explainable set of candidate prices inside the structural
// zone (the proximal edge, the proximal edge of the base's bodies, the midpoint, a deep defensive price, and the two prices where a constraint is exactly met: the
// stop cap and the minimum target room) and judges each against the SAME facts a trade is judged on: the true structural invalidation (never moved), the effective
// risk after the instrument floor, the stop cap, the room to the nearest opposing structure, the reward:risk, and whether the entry band that would result is inside
// the zone and wide enough to be published.
//
// Selection is deterministic and says what it is: the SHALLOWEST feasible candidate, because a price nearer the market is the one most likely to be filled by the
// reaction the setup is about, and a deeper price is chosen only when every shallower one fails a constraint. It is never "the smallest stop": a deeper entry always
// has a smaller stop and a larger reward:risk, which is exactly why neither is a reason to pick it. Nothing is stretched to make a candidate fit: the invalidation
// is not moved, the zone is not widened, the band is the part of the zone that exists. If no candidate is feasible the setup is refused.

// Entry candidate kinds.
const (
	EntryProximal     = "proximal"
	EntryBodyProximal = "body_proximal"
	EntryMidpoint     = "midpoint"
	EntryDeep         = "deep"
	EntryStopCapFit   = "stop_cap_fit"
	EntryRoomFit      = "room_fit"
)

// Entry rejection reasons of a candidate.
const (
	EntryRejectedOutsideZone = "outside_zone"
	EntryRejectedBandTooThin = "band_below_minimum_width"
	EntryRejectedStopCap     = "stop_beyond_cap"
	EntryRejectedNoRoom      = "no_target_room"
	EntryRejectedDegenerate  = "degenerate_risk"
	EntryRejectedBandMarket  = "band_unreachable"
	entryDeepFraction        = 0.75
	entryTickFraction        = 0.5
)

// EntrySpec is what the planner reads. All prices are in the instrument's price units; Sell is true for a supply zone (the proximal edge is the zone's LOW and
// depth runs upward), false for demand (the proximal edge is the HIGH and depth runs downward).
type EntrySpec struct {
	Sell                  bool
	StructLow, StructHigh float64
	// BodyEdge is the proximal edge of the base's bodies (the lowest body edge of a supply base, the highest of a demand base); 0 or outside the zone = unknown.
	BodyEdge     float64
	Invalidation float64
	// MaxBandWidth is the widest entry band that is published (entry_max_width_price); MinBandWidth the narrowest (the instrument's minimum entry zone, 0 = none).
	MaxBandWidth, MinBandWidth float64
	// MaxDepthFraction is how deep into the zone an entry may be, as a share of the structure from the proximal edge (1 = anywhere). Beyond it a price is untested depth
	// the setup gives no support for, and no candidate is offered there (a deep price is chosen only where the zone itself supports it).
	MaxDepthFraction float64
	Budget           StopBudget
	MinimumTargetR   float64
	FallbackTargetR  float64
	Tick             float64
	// Obstacles returns the opposing structure beyond reference in the trade's direction, nearest first (below a SELL entry, above a BUY entry).
	Obstacles func(reference float64) []float64
	// LimitTarget limits a target by the canonical opposing barrier ahead of the entry (the same book the Algo Bot's target-room check and the quality room read): it
	// returns the target to publish, never beyond a blocking opposing zone, and false when the entry has no room before the barrier at all. Nil means no barrier book.
	LimitTarget func(entry, target float64) (limited float64, room bool)
}

// EntryCandidate is one evaluated price.
type EntryCandidate struct {
	Kind          string
	Price         float64
	DepthFraction float64 // 0 at the proximal edge, 1 at the distal edge
	Feasible      bool
	Rejected      string // the first constraint it failed, empty when feasible
	StopPips      float64
	EffectiveRisk float64 // price units, after the instrument floor
	RoomR         float64 // distance to the target over the effective risk
	Target        float64
	TargetLabel   string
	BandLow       float64
	BandHigh      float64
}

// EntryPlan is the evaluation and its selection.
type EntryPlan struct {
	Candidates []EntryCandidate // shallowest first
	Selected   int              // index into Candidates, -1 when none is feasible
	// Refusal is the reason a setup with no feasible candidate is refused: the most common constraint among the candidates that were inside the zone.
	Refusal string
}

// Chosen returns the selected candidate.
func (p EntryPlan) Chosen() (EntryCandidate, bool) {
	if p.Selected < 0 || p.Selected >= len(p.Candidates) {
		return EntryCandidate{}, false
	}
	return p.Candidates[p.Selected], true
}

// PlanEntry evaluates the candidate prices of one zone and selects the shallowest feasible one.
func PlanEntry(spec EntrySpec) EntryPlan {
	plan := EntryPlan{Selected: -1}
	low, high := spec.StructLow, spec.StructHigh
	if !(high > low) || spec.Obstacles == nil {
		return plan
	}
	proximal, distal := low, high
	sign := 1.0 // depth direction: up for supply
	if !spec.Sell {
		proximal, distal, sign = high, low, -1
	}
	height := distal - proximal // signed: positive for supply, negative for demand
	tick := spec.Tick
	if !(tick > 0) {
		tick = 1e-9
	}
	at := func(fraction float64) float64 { return proximal + fraction*height }

	type seed struct {
		kind  string
		price float64
	}
	seeds := []seed{{EntryProximal, proximal}}
	if spec.BodyEdge != 0 && inside(spec.BodyEdge, low, high) && math.Abs(spec.BodyEdge-proximal) > tick {
		seeds = append(seeds, seed{EntryBodyProximal, spec.BodyEdge})
	}
	seeds = append(seeds, seed{EntryMidpoint, at(0.5)}, seed{EntryDeep, at(entryDeepFraction)})
	// The two exact constraint prices.
	b := spec.Budget
	if b.Active() && b.MaxPips > 0 {
		capPrice := b.MaxPips * b.PipSize
		// A SELL entry deeper than the proximal edge risks less; the price where the stop is exactly the cap. Rounded to the tick toward the inside of the cap.
		if spec.Sell {
			seeds = append(seeds, seed{EntryStopCapFit, math.Ceil((spec.Invalidation-capPrice)/tick-1e-6) * tick})
		} else {
			seeds = append(seeds, seed{EntryStopCapFit, math.Floor((spec.Invalidation+capPrice)/tick+1e-6) * tick})
		}
	}
	if spec.MinimumTargetR > 0 {
		// Room: |entry - obstacle| >= minR x |entry - invalidation|, solved for the entry against the nearest obstacle of the proximal price. A SELL: entry - O >= m(inv - entry).
		if list := spec.Obstacles(proximal); len(list) > 0 {
			o, m, inv := list[0], spec.MinimumTargetR, spec.Invalidation
			floor := 0.0
			if b.Active() && b.MinPips > 0 {
				floor = b.MinPips * b.PipSize
			}
			// The risk is the structural distance or the floor, whichever is larger, so the entry must satisfy both bounds; the binding one is the further from the zone's edge.
			var p float64
			if spec.Sell {
				p = math.Ceil(math.Max((o+m*inv)/(1+m), o+m*floor)/tick-1e-6) * tick
			} else {
				p = math.Floor(math.Min((o+m*inv)/(1+m), o-m*floor)/tick+1e-6) * tick
			}
			seeds = append(seeds, seed{EntryRoomFit, p})
		}
	}
	// Every candidate is a price the broker can quote: aligned to the tick toward the inside of the zone (up for a SELL, down for a BUY), never away from the structure, so
	// the published band's near edge IS the recommended price.
	for i := range seeds {
		if spec.Sell {
			seeds[i].price = math.Ceil(seeds[i].price/tick-1e-6) * tick
		} else {
			seeds[i].price = math.Floor(seeds[i].price/tick+1e-6) * tick
		}
	}
	sort.SliceStable(seeds, func(i, j int) bool { return sign*seeds[i].price < sign*seeds[j].price })
	var kept []seed
	for _, s := range seeds {
		if !inside(s.price, low, high) {
			continue // a constraint price outside the zone is no candidate: nothing is stretched to reach it
		}
		if spec.MaxDepthFraction > 0 && height != 0 && (s.price-proximal)/height > spec.MaxDepthFraction+1e-9 {
			continue // deeper than the zone is allowed to be entered
		}
		if n := len(kept); n > 0 && math.Abs(kept[n-1].price-s.price) < entryTickFraction*tick {
			continue // the same price under another name: the first (shallower, more general) name stands
		}
		kept = append(kept, s)
	}

	counts := map[string]int{}
	for _, s := range kept {
		c := evaluate(spec, s.kind, s.price, proximal, distal, height)
		plan.Candidates = append(plan.Candidates, c)
		if c.Rejected != EntryRejectedOutsideZone && !c.Feasible {
			counts[c.Rejected]++
		}
	}
	for i, c := range plan.Candidates {
		if c.Feasible {
			plan.Selected = i
			return plan
		}
	}
	best, bestN := "", 0
	for _, reason := range []string{EntryRejectedStopCap, EntryRejectedNoRoom, EntryRejectedBandTooThin, EntryRejectedDegenerate, EntryRejectedBandMarket} {
		if counts[reason] > bestN {
			best, bestN = reason, counts[reason]
		}
	}
	plan.Refusal = best
	return plan
}

func inside(price, low, high float64) bool { return price >= low-1e-12 && price <= high+1e-12 }

// evaluate judges one price. The band it would publish is [price, price + MaxBandWidth] clipped to the zone (a SELL band runs up from the price, a BUY band down).
func evaluate(spec EntrySpec, kind string, price, proximal, distal, height float64) EntryCandidate {
	c := EntryCandidate{Kind: kind, Price: price}
	if height != 0 {
		c.DepthFraction = (price - proximal) / height
	}
	if !inside(price, spec.StructLow, spec.StructHigh) {
		c.Rejected = EntryRejectedOutsideZone
		return c
	}
	if spec.Sell {
		c.BandLow, c.BandHigh = price, math.Min(distal, price+spec.MaxBandWidth)
	} else {
		c.BandLow, c.BandHigh = math.Max(distal, price-spec.MaxBandWidth), price
	}
	if spec.MinBandWidth > 0 && c.BandHigh-c.BandLow < spec.MinBandWidth-1e-9 {
		c.Rejected = EntryRejectedBandTooThin
		return c
	}
	stop := math.Abs(price - spec.Invalidation)
	if !(stop > 0) || (spec.Sell && spec.Invalidation <= price) || (!spec.Sell && spec.Invalidation >= price) {
		c.Rejected = EntryRejectedDegenerate
		return c
	}
	b := spec.Budget
	if b.Active() {
		c.StopPips = stop / b.PipSize
		if b.MaxPips > 0 && c.StopPips > b.MaxPips+1e-9 {
			c.Rejected = EntryRejectedStopCap
			return c
		}
	}
	c.EffectiveRisk = b.EffectiveRisk(price, spec.Invalidation)
	if list := spec.Obstacles(price); len(list) > 0 {
		c.Target, c.TargetLabel = list[0], "nearest_opposing_structure"
	} else if spec.Sell {
		c.Target, c.TargetLabel = price-spec.FallbackTargetR*c.EffectiveRisk, "fixed_reward_risk"
	} else {
		c.Target, c.TargetLabel = price+spec.FallbackTargetR*c.EffectiveRisk, "fixed_reward_risk"
	}
	// A target is never published beyond a blocking opposing zone: the canonical barrier ahead of the entry limits it, and an entry with no room before it has none.
	if spec.LimitTarget != nil {
		limited, room := spec.LimitTarget(price, c.Target)
		if !room {
			c.Rejected = EntryRejectedNoRoom
			return c
		}
		if limited != c.Target {
			c.Target, c.TargetLabel = limited, "opposing_barrier"
		}
	}
	c.RoomR = math.Abs(c.Target-price) / c.EffectiveRisk
	// The structural obstacle must clear the minimum room as before; the fixed reward:risk fallback is the strategy's own objective and is judged only by what limits it.
	if (c.TargetLabel != "fixed_reward_risk" || c.RoomR < spec.FallbackTargetR-1e-9) && math.Abs(c.Target-price) < spec.MinimumTargetR*c.EffectiveRisk-1e-9 {
		c.Rejected = EntryRejectedNoRoom
		return c
	}
	c.Feasible = true
	return c
}
