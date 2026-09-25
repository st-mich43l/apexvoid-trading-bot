package indicator

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// RollingSimpleATR is an O(1)-per-update incremental equivalent of
// SimpleATR, for the live per-bar-close update path (source task §28:
// "implement an efficient rolling/incremental representation for runtime
// where mathematically equivalent. Correctness first. Benchmark both.").
// SimpleATR itself remains the parity/reference implementation
// (test/indicator/atr_fixture_test.go's golden-master fixtures target it
// directly, and CanonicalATR always uses the batch functions) — this type
// is an additional, provably-equivalent runtime path, not a replacement:
// test/indicator/rolling_atr_test.go asserts RollingSimpleATR.Update,
// called once per candle in order, produces the exact same trailing value
// SimpleATR(candles[:i+1], length)[i] would for every i, over long
// randomized windows — proven, not assumed from the algorithm matching on
// paper.
//
// Mirrors SimpleATR's own warmup convention (min_periods=1): the first
// Update call already returns a real value (that one true-range value),
// never NaN.
type RollingSimpleATR struct {
	length    int
	window    []float64 // circular buffer of the last `length` true-range values
	start     int
	size      int
	sum       float64
	prevClose float64
	hasPrev   bool
}

// NewRollingSimpleATR returns an empty incremental ATR for the given
// length (clamped to at least 1, matching SimpleATR's own clamp).
func NewRollingSimpleATR(length int) *RollingSimpleATR {
	if length < 1 {
		length = 1
	}
	return &RollingSimpleATR{length: length, window: make([]float64, length)}
}

// Update feeds one new closed candle, in chronological order, and returns
// the updated ATR value. Candles must be supplied strictly in the order
// they close — this type has no sequencing/duplicate-detection of its
// own; that is internal/marketdata.TimeframeHistory's job, upstream of
// this call.
func (r *RollingSimpleATR) Update(c market.Candle) float64 {
	tr := trueRangeOne(c, r.prevClose, r.hasPrev)
	r.hasPrev = true
	r.prevClose = c.Close

	if r.size < r.length {
		r.window[(r.start+r.size)%r.length] = tr
		r.sum += tr
		r.size++
	} else {
		idx := r.start
		r.sum -= r.window[idx]
		r.window[idx] = tr
		r.sum += tr
		r.start = (r.start + 1) % r.length
	}
	return r.sum / float64(r.size)
}

// Value returns the current ATR without consuming a new candle. Panics if
// Update has never been called — mirrors market.CandleWindow.Last's
// fail-fast-on-empty discipline rather than returning a misleading 0.
func (r *RollingSimpleATR) Value() float64 {
	if r.size == 0 {
		panic("indicator: RollingSimpleATR.Value called before any Update")
	}
	return r.sum / float64(r.size)
}

// Len reports how many true-range samples are currently in the rolling
// window (<= length).
func (r *RollingSimpleATR) Len() int { return r.size }

// trueRangeOne computes one candle's true range against the previous
// close, matching TrueRange's per-index formula exactly (see
// true_range.go) without needing the full candle slice.
func trueRangeOne(c market.Candle, prevClose float64, hasPrev bool) float64 {
	hl := c.High - c.Low
	if !hasPrev {
		return hl
	}
	hc := absf(c.High - prevClose)
	lc := absf(c.Low - prevClose)
	return maxf(hl, maxf(hc, lc))
}
