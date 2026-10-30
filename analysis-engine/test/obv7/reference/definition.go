package reference

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// On master the order block close-break rule of v6 lived in the engine's instance sources (techniquezone.obClosedThroughRefusal, fed by
// strategies.order_block.confirmed_break_buffer_atr), as the last test on each order block instance. v7 owns the rule in the strategy and the engine no
// longer applies it to the instances, so the vendored v6 applies it itself, to the instances the frames offer, with the value master ran with (0.1 for every
// instrument; the strategy parameter, 0 for v5). The filter only ever removes instances and judges each on its own frame, so wrapping the sources reproduces master's offer exactly.

// withMasterDefinition returns ctx with every frame's Techniques source wrapped by master's close-break rule. ctx itself is not changed.
func withMasterDefinition(ctx *context.MarketContext, breakBufferATR float64) *context.MarketContext {
	if ctx == nil || breakBufferATR <= 0 {
		return ctx
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
			atr := techniquezone.ATRScalar(series, 1)
			for _, in := range inner() {
				if in.Technique == "order_block" && closedThrough(in, bars, atr, series, breakBufferATR) {
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

// closedThrough: any close after the block's own candle, up to the frame's last closed bar, is beyond its far edge by more than the buffer x the frame's ATR
// at that bar (the scalar ATR only where the series is missing). The far edge is the structural one when the entry band was clipped.
func closedThrough(in techniquezone.Instance, bars []market.Candle, atr float64, series []float64, breakBufferATR float64) bool {
	o := in.OriginIndex
	if o < 0 || o >= len(bars)-1 {
		return false
	}
	low, high := in.Low, in.High
	if in.EntryClipped {
		low, high = in.StructuralLow, in.StructuralHigh
	}
	aligned := len(series) == len(bars)
	for i := o + 1; i < len(bars); i++ {
		frameATR := atr
		if aligned && series[i] > 0 && !math.IsNaN(series[i]) {
			frameATR = series[i]
		}
		buffer := breakBufferATR * frameATR
		if in.Side == "buy" && bars[i].Close < low-buffer-1e-9 || in.Side == "sell" && bars[i].Close > high+buffer+1e-9 {
			return true
		}
	}
	return false
}
