package techniquezone

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Derivation helpers. A technique Instance carries the bar it is anchored to,
// not the displacement leg, structure break or gap it was derived from. These
// pure functions recover that derivation from the same legs, breaks, swings and
// bars the zone chain was built on, by re-running the chain's own matching rules
// (SupplyDemand, OrderBlocks, FVG and discoverIFVG), so a consumer can tell two
// structures that are one event from two that are separate. Each returns false
// when the derivation cannot be reproduced; callers must treat that as unknown,
// never as independent.

func legDirection(side string) string {
	if side == "buy" {
		return "up"
	}
	return "down"
}

// LegOfSupplyDemand is the displacement leg a supply/demand instance was cut
// from: SupplyDemand anchors the zone on the bar before the leg starts.
func LegOfSupplyDemand(legs []Leg, originIndex int, side string) (Leg, bool) {
	for _, leg := range legs {
		if leg.Direction == legDirection(side) && leg.Start-1 == originIndex {
			return leg, true
		}
	}
	return Leg{}, false
}

// LegOfOrderBlock is the displacement leg and the structure break an order-block
// instance was cut from: the leg whose last opposite candle is the block's
// origin and which a break caused (OrderBlocks' own rule).
func LegOfOrderBlock(bars []market.Candle, legs []Leg, breaks []Break, originIndex int, side string) (Leg, Break, bool) {
	for _, leg := range legs {
		if leg.Direction != legDirection(side) {
			continue
		}
		bos, ok := causingBOS(leg, breaks)
		if !ok || lastOppositeCandle(bars, leg) != originIndex {
			continue
		}
		return leg, bos, true
	}
	return Leg{}, Break{}, false
}

// BrokenSwing is the swing a break closed beyond: the last swing of the right
// kind before the break at the break's own level (emitBreaks' own rule).
func BrokenSwing(swings []Swing, brk Break) (Swing, bool) {
	kind := "high"
	if brk.Direction == "down" {
		kind = "low"
	}
	for i := len(swings) - 1; i >= 0; i-- {
		s := swings[i]
		if s.Kind == kind && s.Index < brk.Index && s.Price == brk.Level {
			return s, true
		}
	}
	return Swing{}, false
}

// LegContaining is the displacement leg of the given direction that contains
// the bar, if any.
func LegContaining(legs []Leg, index int, direction string) (Leg, bool) {
	for _, leg := range legs {
		if leg.Direction == direction && leg.Start <= index && index <= leg.End {
			return leg, true
		}
	}
	return Leg{}, false
}

// Gap is a three-candle fair-value gap's bar indexes: Older, Middle and Newest
// (the bar the FVG is anchored to and first confirmable on).
type Gap struct{ Older, Middle, Newest int }

func sameBand(aLow, aHigh, bLow, bHigh float64) bool {
	return math.Abs(aLow-bLow) < 1e-6 && math.Abs(aHigh-bHigh) < 1e-6
}

// GapOfFVG verifies that bars[originIndex] closes a fair-value gap of the given
// side whose band is [low, high] (the structural, unclipped band) and returns
// its three bars.
func GapOfFVG(bars []market.Candle, originIndex int, side string, low, high float64) (Gap, bool) {
	i := originIndex
	if i < 2 || i >= len(bars) {
		return Gap{}, false
	}
	older, cur := bars[i-2], bars[i]
	switch {
	case side == "buy" && older.High < cur.Low && sameBand(older.High, cur.Low, low, high):
		return Gap{Older: i - 2, Middle: i - 1, Newest: i}, true
	case side == "sell" && older.Low > cur.High && sameBand(cur.High, older.Low, low, high):
		return Gap{Older: i - 2, Middle: i - 1, Newest: i}, true
	}
	return Gap{}, false
}

// ParentGapOfIFVG is the fair-value gap an inverted-gap instance was made from:
// the earliest gap with the instance's structural band that the first close
// beyond its far bound inverted at invertIndex (discoverIFVG's own scan). A buy
// iFVG is an inverted bearish (supply) gap and a sell iFVG an inverted bullish
// (demand) gap.
func ParentGapOfIFVG(bars []market.Candle, invertIndex int, tradeSide string, low, high float64) (Gap, bool) {
	if invertIndex <= 2 || invertIndex >= len(bars) {
		return Gap{}, false
	}
	gapSide := "buy"
	if tradeSide == "buy" {
		gapSide = "sell"
	}
	for g := 2; g < invertIndex; g++ {
		gap, ok := GapOfFVG(bars, g, gapSide, low, high)
		if !ok {
			continue
		}
		first := -1
		for i := g + 1; i < len(bars); i++ {
			c := bars[i].Close
			if (gapSide == "buy" && c < low) || (gapSide == "sell" && c > high) {
				first = i
				break
			}
		}
		if first == invertIndex {
			return gap, true
		}
	}
	return Gap{}, false
}
