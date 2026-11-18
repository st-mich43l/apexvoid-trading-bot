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
	// ApproachATR is how far price travelled INTO the swing: from the lowest low (a high) or highest high (a low) of the ExternalBars bars before it, to its extreme, in ATR. It is the
	// swing's standing before price left it, so it does not contain the departure (ProminenceATR, the weaker of the two sides, usually IS the departure when the approach was longer).
	ApproachATR float64
	// LeftDominanceBars is how many bars to the left no bar beat the swing (to the start of the data when none did: LeftBounded is then false, and the true
	// dominance may be longer). RightDominanceBars is the same to the right, up to the last closed bar.
	LeftDominanceBars  int
	LeftBounded        bool
	RightDominanceBars int
	External           bool
	Shift              string
	ShiftAt            int64 // open time of the bar whose close made the break; 0 without one
	// ShiftLevel and ShiftLevelTime are the structure the shift broke: the price and the open time of the significant opposite swing whose level was closed through.
	ShiftLevel     float64
	ShiftLevelTime int64
	Relevant       bool
	// Exceeded is true when a later bar traded beyond the swing (a wick counts here, a close ends its relevance).
	Exceeded bool
}

// pivotRead is everything the classification measures about one raw pivot from the bars given: used for the swing itself and, with the same definitions, to decide
// which opposite pivots are significant enough to be structure.
type pivotRead struct {
	pivot       Pivot
	high        bool
	price       float64
	unit        float64
	ok          bool // a volatility read exists at the pivot
	prominence  float64
	departure   float64
	approach    float64
	left        int
	leftBounded bool
	rightEnd    int // the last bar before a later bar exceeded the pivot (the last bar when none did)
	exceeded    bool
}

func (r pivotRead) significant(cfg MajorConfig) bool {
	return r.ok && r.prominence/r.unit >= cfg.IntermediateProminenceATR
}

func readPivot(candles []market.Candle, atr []float64, p Pivot, cfg MajorConfig) pivotRead {
	high := p.Kind == PivotHigh
	price := float64(p.Price)
	i := p.BarIndex
	last := len(candles) - 1
	r := pivotRead{pivot: p, high: high, price: price, unit: atrAt(atr, i)}
	r.ok = r.unit > 0
	if !r.ok {
		return r
	}
	left := -1
	for j := i - 1; j >= 0; j-- {
		if (high && candles[j].High >= price) || (!high && candles[j].Low <= price) {
			left = j
			break
		}
	}
	r.leftBounded = left >= 0
	r.left = i - left - 1
	if !r.leftBounded {
		r.left = i
	}
	right := -1
	for j := i + 1; j <= last; j++ {
		if (high && candles[j].High > price) || (!high && candles[j].Low < price) {
			right = j
			break
		}
	}
	r.rightEnd, r.exceeded = last, right >= 0
	if right >= 0 {
		r.rightEnd = right - 1
	}
	r.prominence = math.Min(moveAway(candles, left+1, i-1, high, price), moveAway(candles, i+1, r.rightEnd, high, price))
	r.departure = moveAway(candles, i+1, minInt(r.rightEnd, i+cfg.DepartureBars), high, price)
	r.approach = moveAway(candles, maxInt(0, i-cfg.ExternalBars), i-1, high, price)
	return r
}

// ClassifyMajor classifies every confirmed pivot of candles. atr is index-aligned with candles (the frame's ATR series). The result is in bar order.
//
// The structure break (shift) is read against STRUCTURE, never against a raw fractal: the reference is the most recent opposite pivot that is itself significant
// (prominence of at least the intermediate threshold, measured from the same bars) and had not been closed through before the swing formed, so a one-bar dip near a top
// is not the structure and a level that was already broken is not broken again. A close through the reference, before the swing was exceeded, is the shift. A level
// that was closed through is evidence ONCE: it is credited to the highest swing between it and the break (the earliest when equal), never to each swing that happens to
// sit above it. CHoCH when the swing extended the move of the previous SIGNIFICANT swing of its kind (or is the first), BOS when it did not.
func ClassifyMajor(candles []market.Candle, atr []float64, cfg MajorConfig) []MajorSwing {
	if cfg.Valid() != nil || len(candles) == 0 {
		return nil
	}
	pivots := DetectPivots(candles, cfg.PivotLeftBars, cfg.PivotRightBars, atr)
	last := len(candles) - 1
	reads := make([]pivotRead, len(pivots))
	for k, p := range pivots {
		reads[k] = readPivot(candles, atr, p, cfg)
	}

	type breakKey struct {
		ref   int // index into pivots of the broken level
		atBar int
	}
	out := make([]MajorSwing, 0, len(pivots))
	shiftOf := map[int]breakKey{} // pivot index -> the break it would take
	owner := map[breakKey]int{}   // break -> the pivot credited with it
	for k, r := range reads {
		if !r.ok {
			continue
		}
		ref, ok := reference(candles, reads, k, cfg)
		if !ok {
			continue
		}
		level := reads[ref].price
		for j := r.pivot.BarIndex + 1; j <= r.rightEnd; j++ {
			if (r.high && candles[j].Close < level) || (!r.high && candles[j].Close > level) {
				key := breakKey{ref: ref, atBar: j}
				shiftOf[k] = key
				if cur, has := owner[key]; !has || outranks(reads[k], reads[cur]) {
					owner[key] = k
				}
				break
			}
		}
	}

	for k, r := range reads {
		if !r.ok {
			continue
		}
		p := r.pivot
		i := p.BarIndex
		ms := MajorSwing{
			Kind: p.Kind, Price: p.Price, BarIndex: i, Time: p.Time, ConfirmedAt: p.ConfirmedAt,
			ProminenceATR: r.prominence / r.unit, DepartureATR: r.departure / r.unit, ApproachATR: r.approach / r.unit,
			LeftDominanceBars: r.left, LeftBounded: r.leftBounded, RightDominanceBars: r.rightEnd - i, Exceeded: r.exceeded,
		}
		ms.External = ms.LeftDominanceBars >= cfg.ExternalBars
		if key, has := shiftOf[k]; has && owner[key] == k {
			ref := reads[key.ref]
			ms.ShiftAt, ms.ShiftLevel, ms.ShiftLevelTime = candles[key.atBar].Time, ref.price, ref.pivot.Time
			ms.Shift = ShiftCHoCH
			if prev, ok := previousSignificant(reads, k, cfg); ok {
				extended := r.price > prev.price
				if !r.high {
					extended = r.price < prev.price
				}
				if !extended {
					ms.Shift = ShiftBOS
				}
			}
		}
		// Relevance: no close beyond the swing since it formed, and not too old.
		ms.Relevant = last-i <= cfg.RelevanceBars
		for j := i + 1; j <= last && ms.Relevant; j++ {
			if (r.high && candles[j].Close > r.price) || (!r.high && candles[j].Close < r.price) {
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

// reference is the structure pivot k's shift is read against: the most recent opposite pivot before it that is significant and was not closed through before pivot k
// formed.
func reference(candles []market.Candle, reads []pivotRead, k int, cfg MajorConfig) (int, bool) {
	i := reads[k].pivot.BarIndex
	for j := k - 1; j >= 0; j-- {
		o := reads[j]
		if o.high == reads[k].high || o.pivot.BarIndex >= i || !o.significant(cfg) {
			continue
		}
		broken := false
		for b := o.pivot.BarIndex + 1; b <= i && !broken; b++ {
			broken = (reads[k].high && candles[b].Close < o.price) || (!reads[k].high && candles[b].Close > o.price)
		}
		if !broken {
			return j, true
		}
	}
	return 0, false
}

// previousSignificant is the most recent significant pivot of the same kind before pivot k.
func previousSignificant(reads []pivotRead, k int, cfg MajorConfig) (pivotRead, bool) {
	for j := k - 1; j >= 0; j-- {
		if reads[j].high == reads[k].high && reads[j].significant(cfg) {
			return reads[j], true
		}
	}
	return pivotRead{}, false
}

// outranks reports whether pivot a takes a break from b: the more extreme one (a higher high, a lower low), the earlier when equal.
func outranks(a, b pivotRead) bool {
	if a.price == b.price {
		return a.pivot.BarIndex < b.pivot.BarIndex
	}
	if a.high {
		return a.price > b.price
	}
	return a.price < b.price
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

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
