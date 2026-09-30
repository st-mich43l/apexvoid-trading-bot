package engine

import (
  "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
  "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
  "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
  "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// momentumTailBars bounds how many trailing candles feed the momentum
// fallback. Velocity needs lookback+1 closes and the 14-bar simple ATR at the
// last bar needs 15 candles; a 64-bar tail gives the identical last-bar result
// as the full frame without rescanning ~1,400 H1 bars per opportunity.
const momentumTailBars = 64

// ClosedHigherTimeframeBiases reads the canonical, already-calculated H1 and
// H4 structure. Candle.Time is its OPEN timestamp: a higher bar is
// unavailable until its own close is no later than the observed bar's close.
// Stale frames are omitted. When a fresh frame's structure is undecided (every
// layer range/unknown) the bias falls back to that SAME higher timeframe's
// price momentum (layer "momentum"; a port of Algo Bot's
// analysis/engine.py::_bias_from_tf), and is omitted only when momentum is
// neutral too. It is never filled from the primary-timeframe bias, and a
// decided structure is never overridden by momentum. The result order is
// deterministic (H1 then H4).
func ClosedHigherTimeframeBiases(
  ctx *context.MarketContext,
  observedTF market.Timeframe,
  observedAt int64,
  momentumCfg momentum.Config,
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
    direction, layer := context.DeriveBias(frame.Structure).Direction, ""
    if direction.IsValid() {
      layer = context.DeriveBias(frame.Structure).Layer.String()
    } else {
      direction, layer = momentumDirection(frame.Candles, momentumCfg), "momentum"
      if !direction.IsValid() {
        continue
      }
    }
    result = append(result, opportunity.HigherTimeframeBias{
      Timeframe: tf, Direction: direction, Layer: layer, ReferenceTime: latest.Time,
    })
  }
  return result
}

// momentumDirection maps the higher timeframe's momentum state to a trade
// direction; a neutral read yields the zero (invalid) direction.
func momentumDirection(candles []market.Candle, cfg momentum.Config) market.Direction {
  if len(candles) > momentumTailBars {
    candles = candles[len(candles)-momentumTailBars:]
  }
  switch momentum.Classify(candles, nil, cfg).State {
  case momentum.Bull:
    return market.Buy
  case momentum.Bear:
    return market.Sell
  default:
    return ""
  }
}
