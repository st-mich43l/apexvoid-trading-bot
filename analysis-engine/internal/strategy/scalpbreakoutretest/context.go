package scalpbreakoutretest

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// scalpContext is the part of the frozen M5 scalp context snapshot the
// breakout discovery reads: the setup volatility, the M1 volatility sizing the
// stop buffer, the setup window's key levels and the corridor room around the
// price the snapshot was taken at.
type scalpContext struct {
	ATR          float64
	M1ATR        float64
	KeyLevels    []keyLevel
	BuyRoomPips  float64
	SellRoomPips float64
}

// buildContext reproduces the snapshot the Python lane held when it evaluated
// the closed M1 bar `now`. The snapshot was rebuilt at every M5 boundary and
// kept while younger than the freshness limit; once older it was rebuilt on
// every cycle. So the snapshot's price and M1 volatility are those of the
// first M1 bar after the latest M5 close while that is still fresh, and of the
// current M1 bar afterwards. The M5 window is the same either way.
func buildContext(cfg Config, m5 []market.Candle, m1All []market.Candle, now int64) (scalpContext, bool) {
	if len(m5) == 0 || len(m1All) == 0 {
		return scalpContext{}, false
	}
	idx := len(m1All) - 1
	lastOpen := m5[len(m5)-1].Time
	if now-lastOpen <= cfg.ContextMaxAgeSeconds {
		boundary := lastOpen + 300
		for i := len(m1All) - 1; i >= 0 && m1All[i].Time >= boundary; i-- {
			if m1All[i].Time == boundary {
				idx = i
				break
			}
		}
	}
	price := m1All[idx].Close
	if math.IsNaN(price) || math.IsInf(price, 0) || price <= 0 {
		return scalpContext{}, false
	}
	active := tail(m5, cfg.ActiveRangeBars)
	activeLow, activeHigh := active[0].Low, active[0].High
	for _, b := range active[1:] {
		activeLow = math.Min(activeLow, b.Low)
		activeHigh = math.Max(activeHigh, b.High)
	}
	if activeHigh <= activeLow {
		return scalpContext{}, false
	}
	pip := cfg.PipSize
	c := scalpContext{
		ATR:          setupATR(m5, cfg.ContextATRBars, pip),
		M1ATR:        m1ATR(tail(m1All[:idx+1], cfg.ConfirmationWindowBars), cfg.ContextATRBars, pip),
		BuyRoomPips:  (activeHigh - price) / pip,
		SellRoomPips: (price - activeLow) / pip,
	}
	atr := techniquezone.ATRSeries(m5, cfg.ContextATRBars)
	swings := techniquezone.FindSwings(m5, cfg.SwingFractalN, 0, 1, atr, -1)
	for _, level := range techniquezone.KeyLevels(swings, atr, cfg.LevelClusterATR, cfg.LevelRoundStep, cfg.LevelMinimumTouches, cfg.LevelMaxClusterSpan, nil) {
		c.KeyLevels = append(c.KeyLevels, keyLevel{Price: level.Price, Touches: level.Touches})
	}
	return c, true
}

// setupATR is the frozen scalp context's M5 volatility: the mean of the last
// `length` highs minus the mean of the last `length` lows.
func setupATR(m5 []market.Candle, length int, pip float64) float64 {
	fallback := math.Max(pip*50, pip)
	if len(m5) < 2 {
		return fallback
	}
	window := tail(m5, length)
	var hi, lo float64
	for _, b := range window {
		hi += b.High
		lo += b.Low
	}
	atr := hi/float64(len(window)) - lo/float64(len(window))
	if atr <= 0 || math.IsNaN(atr) {
		return fallback
	}
	return atr
}

// m1ATR is the mean true range of the last `length` M1 bars; it needs more
// than `length` bars to be warm.
func m1ATR(m1 []market.Candle, length int, pip float64) float64 {
	fallback := math.Max(pip*3, pip)
	if len(m1) < 15 {
		return fallback
	}
	var sum float64
	window := tail(m1, length)
	start := len(m1) - len(window)
	for i := start; i < len(m1); i++ {
		r := m1[i].High - m1[i].Low
		if i > 0 {
			prev := m1[i-1].Close
			r = math.Max(r, math.Max(math.Abs(m1[i].High-prev), math.Abs(m1[i].Low-prev)))
		}
		sum += r
	}
	v := sum / float64(len(window))
	if v > 0 && !math.IsNaN(v) && !math.IsInf(v, 0) {
		return v
	}
	return fallback
}
