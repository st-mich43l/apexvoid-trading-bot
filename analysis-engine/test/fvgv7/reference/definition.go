package reference

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// On master the gap definition of v6 lived in the engine's instance sources (techniquezone.fvgDefinitionRefusal, fed by strategies.fvg.confirmed_*), as the
// last test on each fair value gap instance. v7 owns the definition in the strategy and the engine no longer applies it, so the vendored v6 applies it
// itself, to the instances the frames offer, with the values master ran with (0.5 / 0.5 / 0.5 for every instrument). The filter only ever removes
// instances and judges each on its own frame as of its third candle, so wrapping the sources reproduces master's offer exactly.
const (
	minGapATR              = 0.5
	minDisplacementBodyATR = 0.5
	minDisplacementRatio   = 0.5
)

// withMasterDefinition returns ctx with every frame's Techniques source wrapped by master's gap definition. ctx itself is not changed.
func withMasterDefinition(ctx *context.MarketContext) *context.MarketContext {
	if ctx == nil {
		return nil
	}
	out := *ctx
	out.Timeframes = make(map[market.Timeframe]*context.TimeframeContext, len(ctx.Timeframes))
	for tf, frame := range ctx.Timeframes {
		if frame == nil || frame.Legacy == nil || frame.Legacy.Techniques == nil {
			out.Timeframes[tf] = frame
			continue
		}
		wrapped := *frame
		legacy := *frame.Legacy
		inner := frame.Legacy.Techniques
		bars, series := legacy.Bars, legacy.ATR
		legacy.Techniques = func() []techniquezone.Instance {
			var kept []techniquezone.Instance
			for _, in := range inner() {
				if in.Technique == "fvg" && definitionRefusal(in, bars, techniquezone.ATRScalar(series, 1), series) != "" {
					continue
				}
				kept = append(kept, in)
			}
			return kept
		}
		wrapped.Legacy = &legacy
		out.Timeframes[tf] = &wrapped
	}
	return &out
}

func definitionRefusal(in techniquezone.Instance, bars []market.Candle, atr float64, series []float64) string {
	o := in.OriginIndex
	if o < 2 || o >= len(bars) {
		return ""
	}
	frameATR := atr
	if len(series) == len(bars) && !math.IsNaN(series[o]) && series[o] > 0 {
		frameATR = series[o]
	}
	low, high := in.Low, in.High
	if in.EntryClipped {
		low, high = in.StructuralLow, in.StructuralHigh
	}
	if high-low+1e-9 < minGapATR*frameATR {
		return "gap_small"
	}
	middle := bars[o-1]
	body, span := math.Abs(middle.Close-middle.Open), middle.High-middle.Low
	if body+1e-9 < minDisplacementBodyATR*frameATR || span <= 0 || body/span+1e-9 < minDisplacementRatio {
		return "displacement_weak"
	}
	if (in.Side == "buy" && !(middle.Close > middle.Open)) || (in.Side == "sell" && !(middle.Close < middle.Open)) {
		return "displacement_direction"
	}
	return ""
}
