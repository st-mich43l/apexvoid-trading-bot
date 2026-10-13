package structuralconfluence

import (
	"math"
	"sort"
)

// Side is the trade side a structure supports: "buy" (demand) or "sell" (supply).
const (
	Buy  = "buy"
	Sell = "sell"
)

// Source types, the technique labels of the zone chain's instances.
const (
	SourceSupplyDemand = "supply_demand"
	SourceOrderBlock   = "order_block"
	SourceFVG          = "fvg"
	SourceIFVG         = "ifvg"
	SourceCRT          = "crt"
)

// Unverified reasons: why a fact's derivation could not be reproduced.
const (
	UnverifiedOriginOutsideWindow = "origin_outside_window"
	UnverifiedSupplyDemandLeg     = "supply_demand_leg_not_found"
	UnverifiedOrderBlockLeg       = "order_block_leg_not_found"
	UnverifiedBrokenSwing         = "order_block_broken_swing_not_found"
	UnverifiedGap                 = "fvg_gap_not_found"
	UnverifiedParentGap           = "ifvg_parent_gap_not_found"
	UnverifiedCRT                 = "crt_instance_embeds_reaction"
	UnverifiedTechnique           = "unknown_technique"
	UnverifiedNoAtoms             = "no_provenance_atoms"
)

// Fact is one structure, with the provenance needed to tell whether it is
// independent of another and the time it became confirmable.
//
// Low/High are the ELIGIBLE bounds: the band the zone chain publishes as
// tradable (the proximal slice of a wide zone). StructuralLow/StructuralHigh are
// the original, unclipped bounds. Confluence geometry works on the eligible
// bounds, the same population every strategy trades.
type Fact struct {
	// ID is the canonical structure identity: source, side, origin candle open
	// time and structural bounds. The same structure always has the same ID.
	ID     string
	Source string
	Side   string

	Low, High                     float64
	StructuralLow, StructuralHigh float64

	// FormedAt is the open time of the candle the structure is anchored to.
	FormedAt int64
	// AvailableAt is the CLOSE time of the first candle on which the structure
	// was confirmable. It may be used by a reaction whose touch candle opened at
	// or after it, never by one that opened earlier.
	AvailableAt int64
	// SourceTimes are the open times of the candles the structure was built from.
	SourceTimes []int64

	// Atoms identify the underlying events the structure derives from (a
	// displacement leg, a structure break, a gap, an inversion, ...). Two facts
	// sharing any atom are one source.
	Atoms []string
	// ParentID is the structure this one was derived from, when it has one.
	ParentID string

	// Verified is false when the derivation could not be reproduced; such a fact
	// is never counted as independent.
	Verified         bool
	UnverifiedReason string

	Touches   int
	Mitigated bool
	// Score is the zone chain's own multi-factor score of the structure.
	Score float64
}

// normalised returns a copy with sorted, de-duplicated atoms.
func (f Fact) normalised() Fact {
	atoms := append([]string(nil), f.Atoms...)
	sort.Strings(atoms)
	out := atoms[:0]
	for i, a := range atoms {
		if a == "" || (i > 0 && a == atoms[i-1]) {
			continue
		}
		out = append(out, a)
	}
	f.Atoms = out
	return f
}

// Overlap is the exact price intersection of two intervals; width is negative
// when they are disjoint and zero when they merely touch.
func Overlap(aLow, aHigh, bLow, bHigh float64) (low, high, width float64) {
	low, high = math.Max(aLow, bLow), math.Min(aHigh, bHigh)
	return low, high, high - low
}
