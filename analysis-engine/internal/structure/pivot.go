package structure

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// PivotKind distinguishes a local high from a local low.
type PivotKind uint8

const (
	PivotHigh PivotKind = iota
	PivotLow
)

func (k PivotKind) String() string {
	if k == PivotHigh {
		return "high"
	}
	return "low"
}

// Pivot is a raw candidate turning point — NOT yet a Swing (source task
// §11: "a raw pivot is not immediately structure"). Only pivots that clear
// PromotionConfig.MinimumExcursionATR become a Swing (see swing.go).
//
// Time is the pivot bar's own timestamp. ConfirmedAt is strictly later —
// the timestamp of the bar that completed the right-bar confirmation
// window. A consumer must never treat a Pivot (or the Swing built from it)
// as known before ConfirmedAt: see docs/analysis/market-structure-v2.md's
// causality section and test/structure/causality_test.go's prefix-
// stability proof.
type Pivot struct {
	Kind        PivotKind
	Price       market.Price
	BarIndex    int
	Time        int64
	ConfirmedAt int64
	// ExcursionPrice is the raw (non-ATR) weaker-side excursion this pivot
	// achieved — see excursion's doc comment. Strength is the same value
	// divided by ATR at BarIndex, the one PromotionConfig thresholds
	// against; see pivotStrength's doc comment.
	ExcursionPrice float64
	Strength       float64
}

// DetectPivots returns every CONFIRMED raw pivot in candles: a bar whose
// High (Low) is strictly the greatest (least) among its leftBars/rightBars
// neighbors on both sides. Causal by construction — a pivot at index i is
// only ever returned once candles[i+rightBars] exists in the input slice,
// so calling this with candles[:T] for any T never returns a pivot whose
// ConfirmedAt is later than candles[T-1].Time (proven in
// test/structure/causality_test.go, not just asserted here). There is no
// separate "provisional, not-yet-confirmed" pivot record — a pivot simply
// does not appear in the result until its right window has closed; see
// docs/analysis/market-structure-v2.md's own note on this deliberate
// scope simplification.
//
// atrSeries must be the SAME canonical series (indicator.CanonicalATR)
// index-aligned with candles — never a second, independently-computed
// ATR (docs/adr's V1 finding, restated at the Structure V2 boundary).
func DetectPivots(candles []market.Candle, leftBars, rightBars int, atrSeries []float64) []Pivot {
	if leftBars < 1 {
		leftBars = 1
	}
	if rightBars < 1 {
		rightBars = 1
	}
	var pivots []Pivot
	for i := leftBars; i+rightBars < len(candles); i++ {
		left := candles[i-leftBars : i]
		right := candles[i+1 : i+1+rightBars]
		c := candles[i]

		atr := atrAt(atrSeries, i)
		if isPivotHigh(c, left, right) {
			weaker := weakerExcursion(c.High, left, right, true)
			pivots = append(pivots, Pivot{
				Kind: PivotHigh, Price: market.Price(c.High), BarIndex: i,
				Time: c.Time, ConfirmedAt: candles[i+rightBars].Time,
				ExcursionPrice: weaker, Strength: strengthFromExcursion(weaker, atr),
			})
		}
		if isPivotLow(c, left, right) {
			weaker := weakerExcursion(c.Low, left, right, false)
			pivots = append(pivots, Pivot{
				Kind: PivotLow, Price: market.Price(c.Low), BarIndex: i,
				Time: c.Time, ConfirmedAt: candles[i+rightBars].Time,
				ExcursionPrice: weaker, Strength: strengthFromExcursion(weaker, atr),
			})
		}
	}
	return pivots
}

func isPivotHigh(c market.Candle, left, right []market.Candle) bool {
	for _, o := range left {
		if o.High >= c.High {
			return false
		}
	}
	for _, o := range right {
		if o.High >= c.High {
			return false
		}
	}
	return true
}

func isPivotLow(c market.Candle, left, right []market.Candle) bool {
	for _, o := range left {
		if o.Low <= c.Low {
			return false
		}
	}
	for _, o := range right {
		if o.Low <= c.Low {
			return false
		}
	}
	return true
}

// weakerExcursion is the raw (non-ATR) excursion on the weaker side (source
// task §12: "left/right excursion... do not simply classify every N-bar
// fractal as an equally meaningful swing"). For a high, excursion is how
// far price fell away from it on each side (High minus the lowest Low in
// that side's window); for a low, symmetric using the highest High. Taking
// the MINIMUM of the two sides means a pivot with a huge move away on only
// one side and a shallow one on the other is not scored as strong as a
// pivot with real excursion on both — a real swing needs both approach and
// departure, not a one-sided spike.
func weakerExcursion(pivotPrice float64, left, right []market.Candle, isHigh bool) float64 {
	leftExc := excursion(pivotPrice, left, isHigh)
	rightExc := excursion(pivotPrice, right, isHigh)
	if rightExc < leftExc {
		return rightExc
	}
	return leftExc
}

func strengthFromExcursion(excursionPrice, atr float64) float64 {
	if atr <= 0 {
		return 0
	}
	return excursionPrice / atr
}

func excursion(pivotPrice float64, side []market.Candle, isHigh bool) float64 {
	if len(side) == 0 {
		return 0
	}
	if isHigh {
		lowest := side[0].Low
		for _, c := range side[1:] {
			if c.Low < lowest {
				lowest = c.Low
			}
		}
		return pivotPrice - lowest
	}
	highest := side[0].High
	for _, c := range side[1:] {
		if c.High > highest {
			highest = c.High
		}
	}
	return highest - pivotPrice
}

func atrAt(series []float64, i int) float64 {
	if i < 0 || i >= len(series) {
		return 0
	}
	return series[i]
}
