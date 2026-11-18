package structure

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Major swing classification (SD-2).
//
// A two-bar fractal is not a major swing. ClassifyMajor takes the confirmed raw pivots of a frame (DetectPivots: causal, a pivot exists only once its right bars
// have closed) and measures what makes a swing structurally meaningful, in the units of that frame's own ATR so XAU and 5-digit FX share one configuration:
//
//	prominence   how far price fell away (rose away) from a high (low) on BOTH sides before a bar that beat it: the high minus the HIGHER of the two saddles
//	             (the lowest low between it and the nearest earlier bar that beat it, and between it and the first later bar that beat it, or the last closed bar).
//	departure    the actual reversal: how far price moved away from it within DepartureBars bars after it.
//	dominance    how many bars on each side no bar beat it (distance to the surrounding higher/lower extremes). External means it held its extreme for at least
//	             ExternalBars bars to its left: the boundary of the surrounding range, not an internal pullback inside one.
//	shift        the structure break the departure made: a CLOSE through the most recent opposite pivot before the swing, before the swing was exceeded. It is a
//	             CHoCH when the swing extended the prevailing move (it was a higher high / lower low than the previous swing of its kind, or the first), a BOS
//	             when it was a lower high / higher low (the move continued against it).
//	relevance    still a structure: no close beyond it since it formed, and not older than RelevanceBars.
//
// MAJOR is external, with the major prominence and departure and a confirmed shift; INTERMEDIATE has the intermediate prominence and departure; everything else is
// MINOR. The thresholds are hypotheses (analysis.yml strategies.supply|demand swing_*): they start from the displacement baseline (1.5 ATR) and are validated by
// replay, never tuned to a result.
//
// Causality: every fact is read from candles[0..len-1], all closed. A swing's identity (kind, bar, price, time, ConfirmedAt) never changes as bars are added; its
// measurements (prominence, departure, dominance to the right, shift, relevance, class) can, because more bars exist, so a class is "as of the last bar given" and a
// decision published on it keeps the class it was published with. ConfirmedAt is the OPEN time of the bar that completed the pivot's right window: the swing is
// known when that bar closes, never earlier.

// MajorClass is the structural class of a swing.
type MajorClass uint8

const (
	SwingMinor MajorClass = iota
	SwingIntermediate
	SwingMajor
)

func (c MajorClass) String() string {
	switch c {
	case SwingMajor:
		return "MAJOR"
	case SwingIntermediate:
		return "INTERMEDIATE"
	default:
		return "MINOR"
	}
}

// Shift kinds.
const (
	ShiftNone  = ""
	ShiftBOS   = "BOS"
	ShiftCHoCH = "CHoCH"
)

// MajorConfig are the thresholds of the classification, all in ATR of the frame or in bars.
type MajorConfig struct {
	PivotLeftBars, PivotRightBars int
	MajorProminenceATR            float64
	IntermediateProminenceATR     float64
	MajorDepartureATR             float64
	IntermediateDepartureATR      float64
	DepartureBars                 int
	ExternalBars                  int
	RelevanceBars                 int
}

// Valid reports a configuration that can classify.
func (c MajorConfig) Valid() error {
	switch {
	case c.PivotLeftBars < 1 || c.PivotRightBars < 1 || c.DepartureBars < 1 || c.ExternalBars < 1 || c.RelevanceBars < 1:
		return fmt.Errorf("structure: major swing bar counts must be >= 1")
	case c.IntermediateProminenceATR <= 0 || c.MajorProminenceATR < c.IntermediateProminenceATR:
		return fmt.Errorf("structure: major prominence must be >= intermediate prominence > 0")
	case c.IntermediateDepartureATR <= 0 || c.MajorDepartureATR < c.IntermediateDepartureATR:
		return fmt.Errorf("structure: major departure must be >= intermediate departure > 0")
	}
	return nil
}

// MajorSwing is one classified swing with the measurements that earned its class.
type MajorSwing struct {
	Kind        PivotKind
	Price       market.Price
	BarIndex    int
	Time        int64
	ConfirmedAt int64 // open time of the bar that completed the right window; known when that bar closes

	Class         MajorClass
	ProminenceATR float64
	DepartureATR  float64
	// LeftDominanceBars is how many bars to the left no bar beat the swing (to the start of the data when none did: LeftBounded is then false, and the true
	// dominance may be longer). RightDominanceBars is the same to the right, up to the last closed bar.
	LeftDominanceBars  int
	LeftBounded        bool
	RightDominanceBars int
	External           bool
	Shift              string
	ShiftAt            int64 // open time of the bar whose close made the break; 0 without one
	Relevant           bool
	// Exceeded is true when a later bar traded beyond the swing (a wick counts here, a close ends its relevance).
	Exceeded bool
}

// ClassifyMajor classifies every confirmed pivot of candles. atr is index-aligned with candles (the frame's ATR series). The result is in bar order.
func ClassifyMajor(candles []market.Candle, atr []float64, cfg MajorConfig) []MajorSwing {
	if cfg.Valid() != nil || len(candles) == 0 {
		return nil
	}
	pivots := DetectPivots(candles, cfg.PivotLeftBars, cfg.PivotRightBars, atr)
	last := len(candles) - 1
	out := make([]MajorSwing, 0, len(pivots))
	for k, p := range pivots {
		high := p.Kind == PivotHigh
		price := float64(p.Price)
		i := p.BarIndex
		unit := atrAt(atr, i)
		if !(unit > 0) {
			continue // no volatility read at the pivot: nothing to normalise by
		}
		beats := func(c market.Candle) bool { // a bar that beats the swing's extreme (or equals it, on the left)
			if high {
				return c.High >= price
			}
			return c.Low <= price
		}
		exceeds := func(c market.Candle) bool {
			if high {
				return c.High > price
			}
			return c.Low < price
		}

		left := -1
		for j := i - 1; j >= 0; j-- {
			if beats(candles[j]) {
				left = j
				break
			}
		}
		leftBounded := left >= 0
		leftMove := moveAway(candles, left+1, i-1, high, price)

		right := -1
		for j := i + 1; j <= last; j++ {
			if exceeds(candles[j]) {
				right = j
				break
			}
		}
		rightEnd := last
		if right >= 0 {
			rightEnd = right - 1
		}
		rightMove := moveAway(candles, i+1, rightEnd, high, price)
		prominence := math.Min(leftMove, rightMove)

		depEnd := minInt(rightEnd, i+cfg.DepartureBars)
		departure := moveAway(candles, i+1, depEnd, high, price)

		ms := MajorSwing{
			Kind: p.Kind, Price: p.Price, BarIndex: i, Time: p.Time, ConfirmedAt: p.ConfirmedAt,
			ProminenceATR: prominence / unit, DepartureATR: departure / unit,
			LeftDominanceBars: i - left - 1, LeftBounded: leftBounded, RightDominanceBars: rightEnd - i,
			Exceeded: right >= 0,
		}
		if !leftBounded {
			ms.LeftDominanceBars = i
		}
		ms.External = ms.LeftDominanceBars >= cfg.ExternalBars

		// Shift: a close through the most recent opposite pivot before the swing, before the swing was exceeded.
		if level, ok := previousOpposite(pivots, k); ok {
			for j := i + 1; j <= rightEnd; j++ {
				broke := candles[j].Close < float64(level.Price)
				if !high {
					broke = candles[j].Close > float64(level.Price)
				}
				if broke {
					ms.ShiftAt = candles[j].Time
					ms.Shift = ShiftCHoCH
					if prev, has := previousSame(pivots, k); has {
						extended := price > float64(prev.Price)
						if !high {
							extended = price < float64(prev.Price)
						}
						if !extended {
							ms.Shift = ShiftBOS
						}
					}
					break
				}
			}
		}

		// Relevance: no close beyond the swing since it formed, and not too old.
		ms.Relevant = last-i <= cfg.RelevanceBars
		for j := i + 1; j <= last && ms.Relevant; j++ {
			if (high && candles[j].Close > price) || (!high && candles[j].Close < price) {
				ms.Relevant = false
			}
		}

		switch {
		case ms.External && ms.ProminenceATR >= cfg.MajorProminenceATR && ms.DepartureATR >= cfg.MajorDepartureATR && ms.Shift != ShiftNone:
			ms.Class = SwingMajor
		case ms.ProminenceATR >= cfg.IntermediateProminenceATR && ms.DepartureATR >= cfg.IntermediateDepartureATR:
			ms.Class = SwingIntermediate
		}
		out = append(out, ms)
	}
	return out
}

// moveAway is the greatest distance price moved away from the swing over bars[from..to] (the lowest low below a high, the highest high above a low): 0 when no
// bar is in the range.
func moveAway(bars []market.Candle, from, to int, high bool, price float64) float64 {
	if from > to || from < 0 || to >= len(bars) {
		return 0
	}
	best := 0.0
	for _, c := range bars[from : to+1] {
		d := c.High - price
		if high {
			d = price - c.Low
		}
		best = math.Max(best, d)
	}
	return best
}

func previousOpposite(pivots []Pivot, k int) (Pivot, bool) {
	for j := k - 1; j >= 0; j-- {
		if pivots[j].Kind != pivots[k].Kind {
			return pivots[j], true
		}
	}
	return Pivot{}, false
}

func previousSame(pivots []Pivot, k int) (Pivot, bool) {
	for j := k - 1; j >= 0; j-- {
		if pivots[j].Kind == pivots[k].Kind {
			return pivots[j], true
		}
	}
	return Pivot{}, false
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
