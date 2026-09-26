package engine

import (
  "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
  "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
  "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// ClosedHigherTimeframeBiases reads only the canonical, already-calculated H1
// and H4 structure. Candle.Time is its OPEN timestamp: a higher bar is
// unavailable until its own close is no later than the observed bar's close.
// Stale and structurally undecided frames are omitted, never filled from the
// primary-timeframe bias. The result order is deterministic (H1 then H4).
func ClosedHigherTimeframeBiases(
  ctx *context.MarketContext,
  observedTF market.Timeframe,
  observedAt int64,
) []opportunity.HigherTimeframeBias {
  if ctx == nil {
    return nil
  }
  observedMinutes, valid := observedTF.Minutes()
  if !valid {
    return nil
  }
  observedClose := observedAt + int64(observedMinutes)*60
  var result []opportunity.HigherTimeframeBias
  for _, tf := range []market.Timeframe{market.H1, market.H4} {
    frame, exists := ctx.Timeframes[tf]
    if !exists || frame == nil || len(frame.Candles) == 0 {
      continue
    }
    minutes, _ := tf.Minutes()
    latest := frame.Candles[len(frame.Candles)-1]
    closeAt := latest.Time + int64(minutes)*60
    // Two HTF bars without a fresh update is a missing-data condition,
    // not permission to reuse an old bias as if it were current.
    if closeAt > observedClose || observedClose-closeAt > int64(minutes)*120 {
      continue
    }
    bias := context.DeriveBias(frame.Structure)
    if !bias.Direction.IsValid() {
      continue
    }
    result = append(result, opportunity.HigherTimeframeBias{
      Timeframe: tf, Direction: bias.Direction, Layer: bias.Layer.String(), ReferenceTime: latest.Time,
    })
  }
  return result
}
