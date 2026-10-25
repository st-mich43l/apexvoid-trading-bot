package manuallevels_test

import (
	"math"
	"sort"
)

// A structure is a price interval on a timeframe: a level is [price-band, price+band], a zone [low, high], a line or a round number a point.
type structure struct {
	Family, TF string
	Low, High  float64
}

func (s structure) key() string {
	if s.TF == "" {
		return s.Family
	}
	return s.Family + "|" + s.TF
}

const tolerance = 0.25 // a family "explains" an entry that lies inside the structure or within this many M5 ATR of it

// proximalEntry is the edge of the entry band the price reaches first: the top for a BUY, the bottom for a SELL. A missing entry_end is a single price.
func proximalEntry(action string, entry float64, entryEnd *float64) float64 {
	if entryEnd == nil || *entryEnd == 0 {
		return entry
	}
	if action == "BUY" {
		return math.Max(entry, *entryEnd)
	}
	return math.Min(entry, *entryEnd)
}

// stopPrice is original_sl when the owner moved the stop since, else sl.
func stopPrice(originalSL, sl *float64) (float64, bool) {
	if originalSL != nil && *originalSL > 0 {
		return *originalSL, true
	}
	if sl != nil && *sl > 0 {
		return *sl, true
	}
	return 0, false
}

// distance is zero inside the interval and the gap to its nearest edge outside it.
func distance(price float64, s structure) float64 {
	switch {
	case price < s.Low:
		return s.Low - price
	case price > s.High:
		return price - s.High
	default:
		return 0
	}
}

// nearest is the structure of the list closest to the price.
func nearest(price float64, list []structure) (structure, float64, bool) {
	best, bestDistance, found := structure{}, math.Inf(1), false
	for _, s := range list {
		if d := distance(price, s); d < bestDistance {
			best, bestDistance, found = s, d, true
		}
	}
	return best, bestDistance, found
}

func explains(distance, atr float64) bool { return atr > 0 && distance <= tolerance*atr+1e-9 }

// protectedBy is the structure the stop protects: the nearest one on the entry side of the stop (its far edge at or beyond the entry side of the stop and
// not past the proximal entry). gap is the distance from that far edge to the stop: the owner's noise buffer.
func protectedBy(action string, entry, stop float64, list []structure) (structure, float64, bool) {
	best, bestGap, found := structure{}, math.Inf(1), false
	for _, s := range list {
		var far, gap float64
		if action == "BUY" {
			far = s.Low
			if far < stop || far > entry {
				continue
			}
			gap = far - stop
		} else {
			far = s.High
			if far > stop || far < entry {
				continue
			}
			gap = stop - far
		}
		if gap < bestGap {
			best, bestGap, found = s, gap, true
		}
	}
	return best, bestGap, found
}

// nearestBeyond is the nearest structure lying wholly beyond the stop (below it for a BUY, above it for a SELL), and its gap to the stop.
func nearestBeyond(action string, stop float64, list []structure) (structure, float64, bool) {
	best, bestGap, found := structure{}, math.Inf(1), false
	for _, s := range list {
		var gap float64
		if action == "BUY" {
			if s.High > stop {
				continue
			}
			gap = stop - s.High
		} else {
			if s.Low < stop {
				continue
			}
			gap = s.Low - stop
		}
		if gap < bestGap {
			best, bestGap, found = s, gap, true
		}
	}
	return best, bestGap, found
}

// acceptedBeyond reports two consecutive closes beyond the structure against the trade (below it for a BUY, above it for a SELL) in the closes given
// oldest first: the level was crossed with acceptance before this trade (a flip), not met for the first time.
func acceptedBeyond(action string, s structure, closes []float64) bool {
	run := 0
	for _, c := range closes {
		beyond := c < s.Low
		if action == "SELL" {
			beyond = c > s.High
		}
		if beyond {
			run++
			if run >= 2 {
				return true
			}
		} else {
			run = 0
		}
	}
	return false
}

func quantile(values []float64, q float64) float64 {
	if len(values) == 0 {
		return 0
	}
	s := append([]float64(nil), values...)
	sort.Float64s(s)
	return s[int(math.Round(q*float64(len(s)-1)))]
}

func round(v float64) float64 { return math.Round(v*100) / 100 }
