package strategyutil

// The M5-context / M1-confirmation scalp lane's shared mechanics, ported from
// the Python scalp lane that ran in the profitable XAU week (app/scalping
// context.py, unified_context.py, strategies.py helpers): the cached M5
// context (volatility, corridor room, price), the stop buffer, the structural
// stop and the 1:2 / 1:1 corridor target. They own no setup thesis.

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// ScalpContextConfig are the context's tunables (strategies.scalping.context).
type ScalpContextConfig struct {
	PipSize                float64
	ContextMaxAgeSeconds   int64
	ContextATRBars         int
	ActiveRangeBars        int
	ConfirmationWindowBars int
}

// ScalpContext is the part of the frozen M5 scalp context snapshot the
// discoveries read.
type ScalpContext struct {
	ATR   float64 // setup (M5) volatility
	M1ATR float64
	// Price and CloseAt are the M1 bar the snapshot was taken at.
	Price        float64
	CloseAt      int64
	ActiveLow    float64
	ActiveHigh   float64
	BuyRoomPips  float64
	SellRoomPips float64
}

// BuildScalpContext reproduces the snapshot the Python lane held when it
// evaluated the closed M1 bar `now`. The snapshot was rebuilt at every M5
// boundary and kept while younger than the freshness limit; once older it was
// rebuilt on every cycle. So its price and M1 volatility are those of the first
// M1 bar after the latest M5 close while that is still fresh, and of the
// current M1 bar afterwards. m5 is the setup window (closed bars).
func BuildScalpContext(cfg ScalpContextConfig, m5, m1All []market.Candle, now int64) (ScalpContext, bool) {
	if len(m5) == 0 || len(m1All) == 0 {
		return ScalpContext{}, false
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
		return ScalpContext{}, false
	}
	active := tailCandles(m5, cfg.ActiveRangeBars)
	activeLow, activeHigh := active[0].Low, active[0].High
	for _, b := range active[1:] {
		activeLow = math.Min(activeLow, b.Low)
		activeHigh = math.Max(activeHigh, b.High)
	}
	if activeHigh <= activeLow {
		return ScalpContext{}, false
	}
	pip := cfg.PipSize
	return ScalpContext{
		ATR:          SetupATR(m5, cfg.ContextATRBars, pip),
		M1ATR:        M1ATR(tailCandles(m1All[:idx+1], cfg.ConfirmationWindowBars), cfg.ContextATRBars, pip),
		Price:        price,
		CloseAt:      m1All[idx].Time + 60,
		ActiveLow:    activeLow,
		ActiveHigh:   activeHigh,
		BuyRoomPips:  (activeHigh - price) / pip,
		SellRoomPips: (price - activeLow) / pip,
	}, true
}

// SetupATR is the frozen scalp context's M5 volatility: the mean of the last
// `length` highs minus the mean of the last `length` lows.
func SetupATR(m5 []market.Candle, length int, pip float64) float64 {
	fallback := math.Max(pip*50, pip)
	if len(m5) < 2 {
		return fallback
	}
	window := tailCandles(m5, length)
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

// M1ATR is the mean true range of the last `length` M1 bars; it needs at least
// 15 bars to be warm.
func M1ATR(m1 []market.Candle, length int, pip float64) float64 {
	fallback := math.Max(pip*3, pip)
	if len(m1) < 15 {
		return fallback
	}
	var sum float64
	window := tailCandles(m1, length)
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

// ScalpBuffer mirrors strategies._stop_buffer: the M1 volatility / spread floor
// beyond a structural level.
func ScalpBuffer(m1ATR, m1ATRMultiple, pip, maximumSpreadPips, spreadMultiple float64) float64 {
	return math.Max(m1ATR*m1ATRMultiple, pip*maximumSpreadPips*spreadMultiple)
}

// ScalpStopPips mirrors strategies._stop_pips: a structural stop narrower than
// the minimum is widened to it; one wider than the maximum is rejected (the
// setup does not fit the risk model — shrinking it would falsify the stop).
func ScalpStopPips(structural, minimum, maximum float64) (float64, bool) {
	value := math.Abs(structural)
	if value <= 0 || maximum < minimum || value > maximum {
		return 0, false
	}
	return math.Max(value, minimum), true
}

// ScalpTarget mirrors strategies._select_target: the first reward:risk of the
// ladder (1:2 then 1:1) whose target clears the minimum net target and fits the
// available corridor room.
func ScalpTarget(direction market.Direction, worst, roomPips, stop, minNet, pip float64, ladder []float64) (price, pips float64, ok bool) {
	if pip <= 0 || stop <= 0 {
		return 0, 0, false
	}
	for _, rr := range ladder {
		targetPips := stop * rr
		if targetPips < minNet || targetPips > roomPips {
			continue
		}
		if direction == market.Buy {
			return worst + targetPips*pip, targetPips, true
		}
		return worst - targetPips*pip, targetPips, true
	}
	return 0, 0, false
}

// ScalpDealingPosition mirrors the scalp context's dealing range: simple
// two-bar fractal swings over the M15 window (ties yield both a high and a low),
// then the dealing-range position of price inside the bracketing pair.
func ScalpDealingPosition(m15 []market.Candle, price float64, cfg fib.Config) (float64, bool) {
	const lookback = 2
	if len(m15) < lookback*2+1 {
		return 0, false
	}
	var swings []structure.Swing
	for i := lookback; i < len(m15)-lookback; i++ {
		hi, lo := m15[i-lookback].High, m15[i-lookback].Low
		for j := i - lookback; j <= i+lookback; j++ {
			hi = math.Max(hi, m15[j].High)
			lo = math.Min(lo, m15[j].Low)
		}
		if m15[i].High >= hi {
			swings = append(swings, structure.Swing{Kind: structure.SwingHigh, Price: market.Price(m15[i].High), Time: m15[i].Time})
		}
		if m15[i].Low <= lo {
			swings = append(swings, structure.Swing{Kind: structure.SwingLow, Price: market.Price(m15[i].Low), Time: m15[i].Time})
		}
	}
	dealing, ok := fib.Resolve(swings, market.Price(price), cfg)
	if !ok {
		return 0, false
	}
	return dealing.Position, true
}

func tailCandles(bars []market.Candle, n int) []market.Candle {
	if n > 0 && len(bars) > n {
		return bars[len(bars)-n:]
	}
	return bars
}

// M1Confirmation is the newest closed M1 bar that confirms an M5 setup.
type M1Confirmation struct {
	Close float64
	Time  int64
}

// ConfirmM1Execution mirrors microstructure.confirm_m1_execution: among the
// newest `lookback` closed M1 bars, the latest one that closes in the trade
// direction and interacted with the level (wick within `tolerance` of it, close
// back on the right side of it). M1 is a confirmation frame, never a source of
// structure.
func ConfirmM1Execution(m1 []market.Candle, direction market.Direction, level, tolerance float64, lookback int) (M1Confirmation, bool) {
	if len(m1) == 0 {
		return M1Confirmation{}, false
	}
	window := lookback
	if window > len(m1) {
		window = len(m1)
	}
	if window < 1 {
		window = 1
	}
	tol := math.Max(0, tolerance)
	for offset := 1; offset <= window; offset++ {
		bar := m1[len(m1)-offset]
		var directional, interacted bool
		if direction == market.Buy {
			directional = bar.Close > bar.Open
			interacted = bar.Low <= level+tol && bar.Close > level
		} else {
			directional = bar.Close < bar.Open
			interacted = bar.High >= level-tol && bar.Close < level
		}
		if directional && interacted {
			return M1Confirmation{Close: bar.Close, Time: bar.Time}, true
		}
	}
	return M1Confirmation{}, false
}
