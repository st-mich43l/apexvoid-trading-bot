package indicator

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// SimpleATR mirrors app/analysis/math_utils.py::atr_series exactly:
//
//	tr.rolling(length, min_periods=1).mean()
//
// A plain rolling mean of true range, with min_periods=1 so short windows
// (even a single candle) still produce a value instead of NaN — unlike
// WilderATR below, this one is well-defined for any non-empty input. This
// is the pervasively-used ATR throughout the canonical analysis pipeline
// (_analyze_tf, zones, swings, momentum, liquidity, structure) — see
// docs/go-analysis-migration-audit.md §2.1.
func SimpleATR(candles []market.Candle, length int) []float64 {
	if length < 1 {
		length = 1
	}
	tr := TrueRange(candles)
	out := make([]float64, len(tr))
	sum := 0.0
	for i, v := range tr {
		sum += v
		windowStart := i - length + 1
		if windowStart < 0 {
			windowStart = 0
		}
		if i >= length {
			sum -= tr[i-length]
		}
		n := i - windowStart + 1
		out[i] = sum / float64(n)
	}
	return out
}

// WilderATR mirrors app/analysis/indicators.py::atr — a thin wrapper over
// pandas_ta.atr(..., mamode="rma") with TA-Lib not installed (confirmed
// against the running venv), which takes the "presma" path:
//
//  1. true range, same formula as TrueRange/SimpleATR.
//  2. seed atr[length-1] = mean(tr[0:length]) (a plain SMA of the first
//     `length` true-range values); atr[i] = NaN for i < length-1.
//  3. for i >= length: atr[i] = atr[i-1] + (tr[i]-atr[i-1])/length — the
//     textbook Wilder recursive smoothing pandas' `ewm(alpha=1/length,
//     adjust=False)` reduces to once seeded this way.
//
// Returns (nil, false) when there are fewer than length+1 candles,
// mirroring pandas_ta's own `v_series(high, length+1)` check, which makes
// atr() return Python `None` rather than a partial series in that case —
// preserved here deliberately (§17/§31), not smoothed over, even though it
// is a sharp edge in the Python original (see the audit's insufficient-
// warmup note: `_last()`/`IndicatorSet.atr` assume a Series and would
// raise on that None in production, a pre-existing Python fragility this
// port does not attempt to fix).
func WilderATR(candles []market.Candle, length int) ([]float64, bool) {
	if length < 1 {
		return nil, false
	}
	if len(candles) < length+1 {
		return nil, false
	}
	tr := TrueRange(candles)
	out := make([]float64, len(tr))
	for i := 0; i < length-1; i++ {
		out[i] = math.NaN()
	}
	seed := 0.0
	for i := 0; i < length; i++ {
		seed += tr[i]
	}
	seed /= float64(length)
	out[length-1] = seed
	alpha := 1.0 / float64(length)
	prev := seed
	for i := length; i < len(tr); i++ {
		prev = prev + (tr[i]-prev)*alpha
		out[i] = prev
	}
	return out, true
}

// AtrAt mirrors app/analysis/math_utils.py::atr_at: the ATR value at index,
// clamped into range, falling back when the series is empty, non-finite,
// or non-positive. series may contain NaN (WilderATR's warmup region);
// a NaN value is not finite, so it correctly falls back too.
//
// Python's atr_at also accepts a bare scalar float/int in place of a
// Series; every real caller (swings.py, zones.py, momentum.py, levels.py,
// regime.py — confirmed by grep) always passes a Series, so that overload
// is intentionally not ported — a caller with a scalar ATR already has it
// as a float64 and has no reason to route it through this function.
func AtrAt(series []float64, index int, fallback float64) float64 {
	if len(series) == 0 {
		return fallback
	}
	if index < 0 {
		index = 0
	}
	if index > len(series)-1 {
		index = len(series) - 1
	}
	v := series[index]
	if !isFinitePositive(v) {
		return fallback
	}
	return v
}

// AtrScalar mirrors app/analysis/math_utils.py::atr_scalar: the MEDIAN of
// the series' finite values (NaNs dropped first, matching pandas'
// `.dropna().median()`), falling back the same way AtrAt does. This is
// deliberately a median, not the latest value — do not "simplify" it to
// series[len(series)-1] when porting a caller.
func AtrScalar(series []float64, fallback float64) float64 {
	if len(series) == 0 {
		return fallback
	}
	clean := make([]float64, 0, len(series))
	for _, v := range series {
		if !math.IsNaN(v) {
			clean = append(clean, v)
		}
	}
	if len(clean) == 0 {
		return fallback
	}
	v := median(clean)
	if !isFinitePositive(v) {
		return fallback
	}
	return v
}

func isFinitePositive(v float64) bool {
	return !math.IsNaN(v) && !math.IsInf(v, 0) && v > 0
}

// median matches pandas' Series.median(): sorted-middle, averaging the two
// middle values for an even-length input. Copies its input via the
// caller (AtrScalar already passes a fresh slice) rather than sorting
// in place on data the caller might still hold a reference to.
func median(values []float64) float64 {
	sorted := append([]float64(nil), values...)
	insertionSort(sorted)
	n := len(sorted)
	if n%2 == 1 {
		return sorted[n/2]
	}
	return (sorted[n/2-1] + sorted[n/2]) / 2
}

// insertionSort is enough here: AtrScalar's input is one ATR window
// (bounded by the caller's lookback, not an unbounded series), so O(n^2)
// never matters and this avoids importing sort for one call site — revisit
// if a future caller feeds it something large.
func insertionSort(values []float64) {
	for i := 1; i < len(values); i++ {
		v := values[i]
		j := i - 1
		for j >= 0 && values[j] > v {
			values[j+1] = values[j]
			j--
		}
		values[j+1] = v
	}
}
