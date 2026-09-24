package zone

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// fvgStrength scores the gap's causal formation evidence: gap width and the
// displacement body between its two boundary candles. Both are normalized by
// ATR and capped independently so one extreme candle cannot dominate.
func fvgStrength(candles []market.Candle, index int, low, high, atr float64) float64 {
	if atr <= 0 || index < 2 || index >= len(candles) || high <= low {
		return 0
	}
	gap := clamp01((high - low) / atr)
	displacement := clamp01(candles[index-1].Body() / atr)
	return 0.6*gap + 0.4*displacement
}

// inversionStrength combines the source zone's established quality with the
// body dominance and penetration of the candle that confirms inversion.
func inversionStrength(source Zone, candle market.Candle, boundary, atr float64) float64 {
	bodyDominance := 0.0
	if candle.Range() > 0 {
		bodyDominance = clamp01(candle.Body() / candle.Range())
	}
	penetration := 0.0
	if atr > 0 {
		penetration = clamp01(math.Abs(candle.Close-boundary) / atr)
	}
	return clamp01(0.5*clamp01(source.Strength) + 0.3*bodyDominance + 0.2*penetration)
}

// flipStrength scores accepted structural displacement and how decisively the
// required close sequence remains beyond the broken level.
func flipStrength(candles []market.Candle, from, bars int, level, atr float64) float64 {
	if atr <= 0 || bars <= 0 || from < 0 || from+bars > len(candles) {
		return 0
	}
	body := clamp01(candles[from].Body() / atr)
	var accepted float64
	for i := from; i < from+bars; i++ {
		accepted += clamp01(math.Abs(candles[i].Close-level) / atr)
	}
	accepted /= float64(bars)
	return 0.6*body + 0.4*accepted
}

// applyLifecycleStrength preserves formation quality while accounting for
// mitigation. Invalidated/spent zones have no usable strength; a partially
// filled FVG decays continuously instead of remaining at formation quality.
func applyLifecycleStrength(z Zone, candles []market.Candle, state State) float64 {
	if state == StateInvalidated || state == StateMitigated {
		return 0
	}
	strength := clamp01(z.Strength)
	if z.Kind == KindFVG || z.Kind == KindIFVG {
		strength *= 1 - 0.5*gapFillFraction(z, candles)
	} else if state == StateTouched {
		strength *= 0.85
	} else if state == StatePartiallyMitigated {
		strength *= 0.65
	}
	return clamp01(strength)
}

func gapFillFraction(z Zone, candles []market.Candle) float64 {
	width := float64(z.High - z.Low)
	if width <= 0 {
		return 1
	}
	start := z.BreakIndex + 1
	if start < 0 {
		start = 0
	}
	fill := 0.0
	for i := start; i < len(candles); i++ {
		if z.Side == Demand && candles[i].Low < float64(z.High) {
			fill = math.Max(fill, (float64(z.High)-candles[i].Low)/width)
		}
		if z.Side == Supply && candles[i].High > float64(z.Low) {
			fill = math.Max(fill, (candles[i].High-float64(z.Low))/width)
		}
	}
	return clamp01(fill)
}

func clamp01(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 1 {
		return 1
	}
	return value
}
