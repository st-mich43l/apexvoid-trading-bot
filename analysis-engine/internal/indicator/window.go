package indicator

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// WilderWindow is the Wilder ATR(n) of a candle series as of any index, computed
// over a FIXED trailing window of window candles ending at that index, never over
// the whole loaded history. Wilder smoothing is recursive, so its value carries a
// decaying residual of its seed; computing it over the whole history would make a
// geometry depend (slightly) on how many candles happened to be loaded. A fixed
// trailing window removes that dependence exactly: for any index with at least
// window candles behind it the value is a pure function of those candles. Each
// window is WilderATR (TR is max(H-L, |H-prevC|, |L-prevC|); ATR[t] =
// (ATR[t-1]*(n-1) + TR[t]) / n, seeded with the mean of the first n true ranges).
//
// It is the one volatility unit the rebuilt strategies (CRT, iFVG, Break & Retest)
// normalise their distances by.
type WilderWindow struct {
	bars    []market.Candle
	length  int
	window  int
	values  []float64
	checked []bool
}

// NewWilderWindow reads bars lazily: nothing is computed until At asks.
func NewWilderWindow(bars []market.Candle, length, window int) *WilderWindow {
	return &WilderWindow{bars: bars, length: length, window: window, values: make([]float64, len(bars)), checked: make([]bool, len(bars))}
}

// At is the ATR as of index i when it exists, is finite and is positive. It needs at
// least length+1 candles ending at i (WilderATR's own minimum).
func (w *WilderWindow) At(i int) (float64, bool) {
	if w == nil || i < 0 || i >= len(w.bars) {
		return 0, false
	}
	if !w.checked[i] {
		w.checked[i] = true
		start := i - w.window + 1
		if start < 0 {
			start = 0
		}
		if series, ok := WilderATR(w.bars[start:i+1], w.length); ok {
			w.values[i] = series[len(series)-1]
		}
	}
	v := w.values[i]
	if math.IsNaN(v) || math.IsInf(v, 0) || v <= 0 {
		return 0, false
	}
	return v, true
}

// Enough reports whether the series is long enough to carry any ATR.
func (w *WilderWindow) Enough() bool { return w != nil && len(w.bars) >= w.length+1 }
