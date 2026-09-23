package fib

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// LevelKind distinguishes a retracement level (inside the swing range)
// from an extension level (projected past it).
type LevelKind uint8

const (
	KindRetracement LevelKind = iota
	KindExtension
)

// retracementRatios/extensionRatios are fibonacci.py's own
// RETRACEMENT_RATIOS/EXTENSION_RATIOS — exact values, not invented.
var (
	retracementRatios = []float64{0.236, 0.382, 0.5, 0.618, 0.786}
	extensionRatios   = []float64{1.0, 1.272, 1.618}
)

// Level is one point on a fib ladder — fibonacci.py's FibLevel.
type Level struct {
	Ratio float64
	Price market.Price
	Kind  LevelKind
}

// Ladder ports fib_ladder: retracements measured back down from high
// toward low (ratio=0 reproduces high, ratio=1 would reproduce low);
// extensions anchored at low (low + ratio*span), so ratio=1.0 reproduces
// high itself and 1.272/1.618 project 127.2%/161.8% of the original leg
// beyond low. Returns nil for a zero-or-inverted span (high <= low),
// same as Python's own `if span <= 0: return []`.
func Ladder(low, high market.Price, includeExtensions bool) []Level {
	lowF, highF := float64(low), float64(high)
	span := highF - lowF
	if span <= 0 {
		return nil
	}
	levels := make([]Level, 0, len(retracementRatios)+len(extensionRatios))
	for _, r := range retracementRatios {
		levels = append(levels, Level{Ratio: r, Price: market.Price(highF - r*span), Kind: KindRetracement})
	}
	if includeExtensions {
		for _, e := range extensionRatios {
			levels = append(levels, Level{Ratio: e, Price: market.Price(lowF + e*span), Kind: KindExtension})
		}
	}
	return levels
}

// NearestLevel ports nearest_fib: the closest level of an allowed Kind
// within epsilonATR*atr of price, or false if none qualifies. kinds
// defaults to retracement-only when none are given — Python's own
// kinds=("retracement",) default.
func NearestLevel(levels []Level, price, atr market.Price, epsilonATR float64, kinds ...LevelKind) (Level, bool) {
	if len(levels) == 0 || atr <= 0 {
		return Level{}, false
	}
	e := epsilonATR
	if e < 0 {
		e = 0
	}
	band := e * float64(atr)
	if band <= 0 {
		return Level{}, false
	}
	if len(kinds) == 0 {
		kinds = []LevelKind{KindRetracement}
	}
	allowed := make(map[LevelKind]bool, len(kinds))
	for _, k := range kinds {
		allowed[k] = true
	}
	priceF := float64(price)
	var best Level
	var bestDist float64
	found := false
	for _, l := range levels {
		if !allowed[l.Kind] {
			continue
		}
		dist := priceF - float64(l.Price)
		if dist < 0 {
			dist = -dist
		}
		if dist <= band && (!found || dist < bestDist) {
			best = l
			bestDist = dist
			found = true
		}
	}
	return best, found
}
