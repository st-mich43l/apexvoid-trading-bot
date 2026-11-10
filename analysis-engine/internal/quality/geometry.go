package quality

import "math"

// Trade-aware geometry of a published entry. Every function takes the trade's direction: a BUY is entered at the proximal (upper) edge of its band and
// is exposed below it, a SELL at the lower edge and is exposed above it. Distances that have a side ("ahead", "chase") are signed by the trade, never
// taken as an absolute value, so a target behind the entry cannot become a reward and a favourable move cannot be read as a chase.

// EntryChase is how far the latest price has run from the entry reference against the trade, and how far it has moved with it, in ATR.
//
//	chase      BUY  max(0, price - reference): price above the proximal edge, the entry is being chased upward
//	           SELL max(0, reference - price): price below the proximal edge, the entry is being chased downward
//	favourable BUY  max(0, reference - price): price deeper in the band than the edge, a better fill
//	           SELL max(0, price - reference)
//
// ok is false without a positive finite ATR or with a non-finite price or reference: there is nothing to measure.
func EntryChase(buy bool, price, reference, atr float64) (chaseATR, favourableATR float64, ok bool) {
	if !(atr > 0) || math.IsInf(atr, 0) || !finite(price) || !finite(reference) {
		return 0, 0, false
	}
	moved := price - reference // positive: above the edge
	if !buy {
		moved = -moved
	}
	return math.Max(0, moved) / atr, math.Max(0, -moved) / atr, true
}

// EntryLocation is entry timing: Fall(chase in ATR; 0.25, 1.5). Price that has run from the proximal edge against the trade is a late entry; price
// back inside the band (a better fill) is not and scores in full. Missing ATR or invalid geometry is missing evidence and scores 0. Whether the price is
// still a valid entry is decided by the strategy's own validity rules, before a score is asked for; this function never turns a deeper price into
// extra credit beyond "not late".
func EntryLocation(buy bool, price, reference, atr float64) float64 {
	chase, _, ok := EntryChase(buy, price, reference, atr)
	if !ok {
		return 0
	}
	return Fall(chase, 0.25, 1.5)
}

// RoomGeometry is the risk and the room of one entry, from the same executable reference.
type RoomGeometry struct {
	// StructuralRisk is the distance from the reference to the structural invalidation, behind the entry; EffectiveRisk is what the trade is exposed to:
	// the larger of that and the instrument's execution stop floor (execution widens a tighter stop to the floor). Zero when invalid.
	StructuralRisk, EffectiveRisk float64
	// Reward is the distance from the reference to the target ahead of the entry (zero or negative: the target is not ahead).
	Reward float64
	// StructuralRoomR and EffectiveRoomR are Reward over each risk; zero when the geometry is invalid.
	StructuralRoomR, EffectiveRoomR float64
	// Score is Room(Reward, EffectiveRisk, structural): 1R scores 0 and 3R scores 1; 0 when the target is not structural, not ahead, or the risk is invalid.
	Score float64
	// Valid is true when the invalidation is behind the entry, the target is ahead of it and every number is finite.
	Valid bool
}

// MeasureRoom measures the room of an entry. reference is the executable proximal edge, invalidation the structural stop, target the objective and
// floor the instrument's execution stop floor in price units (0: none). structural is true only for a target that is observed structure.
func MeasureRoom(buy bool, reference, invalidation, target, floor float64, structural bool) RoomGeometry {
	var g RoomGeometry
	if !finite(reference) || !finite(invalidation) || !finite(target) || !finite(floor) {
		return g
	}
	g.StructuralRisk = reference - invalidation
	g.Reward = target - reference
	if !buy {
		g.StructuralRisk, g.Reward = -g.StructuralRisk, -g.Reward
	}
	if !(g.StructuralRisk > 0) { // the invalidation must be behind the entry
		g.StructuralRisk, g.Reward = 0, 0
		return g
	}
	g.EffectiveRisk = math.Max(g.StructuralRisk, math.Max(0, floor))
	g.StructuralRoomR = math.Max(0, g.Reward) / g.StructuralRisk
	g.EffectiveRoomR = math.Max(0, g.Reward) / g.EffectiveRisk
	g.Valid = g.Reward > 0
	if g.Valid && structural {
		g.Score = Room(g.Reward, g.EffectiveRisk, true)
	}
	return g
}
