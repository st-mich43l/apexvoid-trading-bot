// Package momentum is a port of app/analysis/momentum.py::momentum_state, the
// price-only, ATR-normalized momentum classification: discrete velocity
// v = dC / (n * ATR) and per-bar acceleration a = dv / n over an n-bar
// lookback, thresholded into bull / bear / neutral.
//
// It is pure (no I/O, clock or randomness) and takes the ATR series as an
// argument so callers choose the canonical ATR algorithm.
package momentum

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// State is the discrete momentum classification.
type State string

const (
	Neutral State = "neutral"
	Bull    State = "bull"
	Bear    State = "bear"
)

// Config carries the velocity thresholds (Configuration V3
// analysis.momentum.*).
type Config struct {
	Lookback      int
	BullThreshold float64
	BearThreshold float64
}

// Result is one classification.
type Result struct {
	State        State
	Velocity     float64
	Acceleration float64
	Lookback     int
}

// Classify mirrors momentum_state(df, atr, lookback, bull, bear). atr must be
// aligned with candles (one value per candle), as indicator.SimpleATR returns;
// a nil atr is computed with the 14-bar simple ATR, the Python default.
func Classify(candles []market.Candle, atr []float64, cfg Config) Result {
	n := cfg.Lookback
	if n < 1 {
		n = 1
	}
	if len(candles) < 2 {
		return Result{State: Neutral, Lookback: n}
	}
	if atr == nil {
		atr = indicator.SimpleATR(candles, 14)
	}
	idx := len(candles) - 1
	v := velocityAt(candles, atr, idx, n)
	a := 0.0
	if prev := idx - n; prev >= n {
		a = (v - velocityAt(candles, atr, prev, n)) / float64(n)
	}
	state := Neutral
	switch {
	case v >= cfg.BullThreshold:
		state = Bull
	case v <= cfg.BearThreshold:
		state = Bear
	}
	return Result{State: state, Velocity: v, Acceleration: a, Lookback: n}
}

// velocityAt mirrors _velocity_at: zero when the index or ATR cannot support
// a lookback, otherwise the close change over n bars per unit of n * ATR.
func velocityAt(candles []market.Candle, atr []float64, index, n int) float64 {
	if index < n || index < 0 || index >= len(candles) {
		return 0
	}
	atrValue := indicator.AtrAt(atr, index, 0)
	if atrValue <= 0 {
		return 0
	}
	delta := float64(candles[index].Close) - float64(candles[index-n].Close)
	if math.IsNaN(delta) || math.IsInf(delta, 0) {
		return 0
	}
	return delta / (float64(n) * atrValue)
}
