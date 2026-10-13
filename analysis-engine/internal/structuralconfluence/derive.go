package structuralconfluence

import (
	"fmt"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Frame is what the derivation reads of one timeframe: the bars the structures
// were built on, and the displacement legs, structure breaks and swings the zone
// chain computed from them.
type Frame struct {
	Bars   []market.Candle
	Legs   []techniquezone.Leg
	Breaks []techniquezone.Break
	Swings []techniquezone.Swing
	// PeriodSeconds is one candle's duration. Candle.Time is its OPEN time.
	PeriodSeconds int64
}

func (f Frame) open(i int) int64    { return f.Bars[i].Time }
func (f Frame) closeOf(i int) int64 { return f.Bars[i].Time + f.PeriodSeconds }
func (f Frame) inRange(i int) bool  { return i >= 0 && i < len(f.Bars) }
func legDirectionOf(side string) string {
	if side == Buy {
		return "up"
	}
	return "down"
}

func (f Frame) legAtom(leg techniquezone.Leg) string {
	return fmt.Sprintf("leg:%s:%d", leg.Direction, f.open(leg.Start))
}

func structuralBounds(in techniquezone.Instance) (low, high float64) {
	if in.EntryClipped {
		return in.StructuralLow, in.StructuralHigh
	}
	return in.Low, in.High
}

// FactID is the canonical identity of a structure.
func FactID(source, side string, originOpen int64, structuralLow, structuralHigh float64) string {
	return fmt.Sprintf("%s:%s:%d:%.6f:%.6f", source, side, originOpen, structuralLow, structuralHigh)
}

// DeriveFacts turns zone-chain instances into facts with provenance and an
// availability time. Instances anchored to a different frame (a higher
// timeframe's) must not be passed: their bar indexes are not this frame's.
// The result is sorted by ID and free of duplicate IDs, whatever the input order.
func DeriveFacts(f Frame, instances []techniquezone.Instance) []Fact {
	seen := map[string]bool{}
	var out []Fact
	for _, in := range instances {
		fact := f.derive(in)
		if seen[fact.ID] {
			continue
		}
		seen[fact.ID] = true
		out = append(out, fact.normalised())
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (f Frame) derive(in techniquezone.Instance) Fact {
	sLow, sHigh := structuralBounds(in)
	fact := Fact{
		Source: in.Technique, Side: in.Side, Low: in.Low, High: in.High, StructuralLow: sLow, StructuralHigh: sHigh,
		Touches: in.Touches, Mitigated: in.Mitigated, Score: in.ZoneScore,
	}
	unverified := func(reason string) Fact {
		fact.Verified, fact.UnverifiedReason = false, reason
		if fact.ID == "" {
			fact.ID = FactID(in.Technique, in.Side, 0, sLow, sHigh)
		}
		return fact
	}
	if in.Technique == SourceCRT {
		return unverified(UnverifiedCRT)
	}
	if !f.inRange(in.OriginIndex) {
		return unverified(UnverifiedOriginOutsideWindow)
	}
	fact.FormedAt = f.open(in.OriginIndex)
	fact.ID = FactID(in.Technique, in.Side, fact.FormedAt, sLow, sHigh)
	available := in.OriginIndex
	var atoms []string
	var times []int64

	switch in.Technique {
	case SourceSupplyDemand:
		leg, ok := techniquezone.LegOfSupplyDemand(f.Legs, in.OriginIndex, in.Side)
		if !ok {
			return unverified(UnverifiedSupplyDemandLeg)
		}
		// The zone is cut from the three candles before the leg and from the leg itself.
		atoms = append(atoms, f.legAtom(leg))
		atoms = append(atoms, f.candleAtoms(maxInt(0, leg.Start-3), leg.End)...)
		times = append(times, f.open(in.OriginIndex), f.open(leg.Start), f.open(leg.End))
		available = leg.End

	case SourceOrderBlock:
		leg, brk, ok := techniquezone.LegOfOrderBlock(f.Bars, f.Legs, f.Breaks, in.OriginIndex, in.Side)
		if !ok {
			return unverified(UnverifiedOrderBlockLeg)
		}
		swing, ok := techniquezone.BrokenSwing(f.Swings, brk)
		if !ok {
			return unverified(UnverifiedBrokenSwing)
		}
		confirmed := swing.ConfirmedIndex
		if !f.inRange(confirmed) {
			confirmed = swing.Index
		}
		atoms = append(atoms, f.legAtom(leg), fmt.Sprintf("break:%s:%d", brk.Direction, f.open(brk.Index)))
		atoms = append(atoms, f.candleAtoms(in.OriginIndex, in.OriginIndex)...)
		atoms = append(atoms, f.candleAtoms(leg.Start, leg.End)...)
		times = append(times, f.open(in.OriginIndex), f.open(leg.Start), f.open(leg.End), f.open(brk.Index), f.open(swing.Index))
		// The block exists once the leg is complete AND the break it was caused by
		// is confirmable: the break closes beyond a swing that is itself only a
		// fractal swing once its confirming candles have closed.
		available = maxInt(leg.End, maxInt(brk.Index, confirmed))

	case SourceFVG:
		gap, ok := techniquezone.GapOfFVG(f.Bars, in.OriginIndex, in.Side, sLow, sHigh)
		if !ok {
			return unverified(UnverifiedGap)
		}
		atoms = append(atoms, f.gapAtom(gap))
		atoms = append(atoms, f.candleAtoms(gap.Older, gap.Newest)...)
		if leg, found := techniquezone.LegContaining(f.Legs, gap.Middle, legDirectionOf(in.Side)); found {
			atoms = append(atoms, f.legAtom(leg))
		}
		times = append(times, f.open(gap.Older), f.open(gap.Middle), f.open(gap.Newest))
		available = gap.Newest

	case SourceIFVG:
		gap, ok := techniquezone.ParentGapOfIFVG(f.Bars, in.OriginIndex, in.Side, sLow, sHigh)
		if !ok {
			return unverified(UnverifiedParentGap)
		}
		parentSide := Buy
		if in.Side == Buy {
			parentSide = Sell
		}
		atoms = append(atoms, f.gapAtom(gap), fmt.Sprintf("inversion:%d", f.open(in.OriginIndex)))
		atoms = append(atoms, f.candleAtoms(gap.Older, gap.Newest)...)
		atoms = append(atoms, f.candleAtoms(in.OriginIndex, in.OriginIndex)...)
		if leg, ok := techniquezone.LegContaining(f.Legs, gap.Middle, legDirectionOf(parentSide)); ok {
			atoms = append(atoms, f.legAtom(leg))
		}
		// The candle that inverts the gap belongs to the displacement that
		// inverted it; a structure cut from that same displacement is one event
		// with the inversion.
		if leg, ok := techniquezone.LegContaining(f.Legs, in.OriginIndex, legDirectionOf(in.Side)); ok {
			atoms = append(atoms, f.legAtom(leg))
		}
		fact.ParentID = FactID(SourceFVG, parentSide, f.open(gap.Newest), sLow, sHigh)
		times = append(times, f.open(gap.Older), f.open(gap.Middle), f.open(gap.Newest), f.open(in.OriginIndex))
		available = in.OriginIndex

	default:
		return unverified(UnverifiedTechnique)
	}
	if len(atoms) == 0 {
		return unverified(UnverifiedNoAtoms)
	}
	sort.Slice(times, func(i, j int) bool { return times[i] < times[j] })
	fact.Atoms, fact.SourceTimes, fact.Verified = atoms, times, true
	fact.AvailableAt = f.closeOf(available)
	return fact
}

// candleAtoms are the source candles of a structure: two structures built from a
// common candle are one event whatever they are called (an FVG whose middle
// candle is the candle that inverts a gap; an FVG cut from the three candles of a
// supply/demand base; a base that is another zone's displacement).
func (f Frame) candleAtoms(from, to int) []string {
	var out []string
	for i := from; i <= to; i++ {
		if f.inRange(i) {
			out = append(out, fmt.Sprintf("candle:%d", f.open(i)))
		}
	}
	return out
}

func (f Frame) gapAtom(g techniquezone.Gap) string {
	return fmt.Sprintf("gap:%d", f.open(g.Older))
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
