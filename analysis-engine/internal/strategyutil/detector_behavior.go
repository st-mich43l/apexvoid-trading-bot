package strategyutil

// This file contains the small, shared pieces of the frozen Python detector
// contract used by more than one strategy.  It intentionally consumes the
// canonical MarketContext; it never rebuilds structure, zones or liquidity.

import (
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

type LiquidityGrab struct {
	Pool  liquidity.Pool
	Grade string
}

// dealingRange is the premium/discount read the frozen detector contract gates
// on: the detector-contract frame's range when the engine computed one,
// otherwise the canonical dealing range.
func dealingRange(tf *analysiscontext.TimeframeContext) *fib.DealingRange {
	if tf == nil {
		return nil
	}
	if tf.Legacy != nil {
		return tf.Legacy.Range
	}
	return tf.Fib.Range
}

// regimeOf is the regime the frozen detector contract gates on.
func regimeOf(tf *analysiscontext.TimeframeContext) regime.State {
	if tf.Legacy != nil {
		return tf.Legacy.Regime
	}
	return tf.Regime
}

// InChop reports the frozen detector contract's chop state for the timeframe.
func InChop(tf *analysiscontext.TimeframeContext) bool {
	return tf != nil && regimeOf(tf).Kind == "chop"
}

func PremiumDiscountAllows(tf *analysiscontext.TimeframeContext, direction market.Direction, strict bool) bool {
	dealing := dealingRange(tf)
	if dealing == nil {
		return true
	}
	if dealing.Zone == "eq" {
		return false
	}
	if direction == market.Buy {
		if strict {
			return dealing.Zone == "discount"
		}
		return dealing.Zone != "premium"
	}
	if strict {
		return dealing.Zone == "premium"
	}
	return dealing.Zone != "discount"
}

func LiveZones(tf *analysiscontext.TimeframeContext, direction market.Direction, price, atr, maximumDistanceATR float64) []zone.Zone {
	if tf == nil {
		return nil
	}
	want := zone.Demand
	if direction == market.Sell {
		want = zone.Supply
	}
	var out []zone.Zone
	for _, z := range tf.Zones.Zones {
		if z.Side != want || z.State == zone.StateInvalidated || z.State == zone.StateMitigated {
			continue
		}
		low, high := float64(z.Low), float64(z.High)
		if direction == market.Buy && price < low || direction == market.Sell && price > high {
			continue
		}
		distance := 0.0
		if price > high {
			distance = price - high
		} else if price < low {
			distance = low - price
		}
		if atr > 0 && distance > maximumDistanceATR*atr {
			continue
		}
		out = append(out, z)
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].LegacyScore != out[j].LegacyScore {
			return out[i].LegacyScore > out[j].LegacyScore
		}
		if out[i].Strength != out[j].Strength {
			return out[i].Strength > out[j].Strength
		}
		di, dj := distanceToBand(price, float64(out[i].Low), float64(out[i].High)), distanceToBand(price, float64(out[j].Low), float64(out[j].High))
		if di != dj {
			return di < dj
		}
		return out[i].ID < out[j].ID
	})
	return out
}

func NearestValidLevel(tf *analysiscontext.TimeframeContext, direction market.Direction, price float64) *keylevel.Level {
	if tf == nil {
		return nil
	}
	bestDistance := math.Inf(1)
	var best *keylevel.Level
	for i := range tf.KeyLevel.Levels {
		level := &tf.KeyLevel.Levels[i]
		p := float64(level.Price)
		if direction == market.Buy && p > price || direction == market.Sell && p < price {
			continue
		}
		if d := math.Abs(price - p); d < bestDistance {
			bestDistance, best = d, level
		}
	}
	return best
}

func EntryBandForLevel(level keylevel.Level, atr, proximalATR float64) (float64, float64) {
	half := math.Max(level.Band, proximalATR*atr)
	return float64(level.Price) - half, float64(level.Price) + half
}

// GrabForBand applies the liquidity-grab contract to the
// canonical pools. A sweep is a wick through the pool band, and the same
// candle may also reclaim it. A clean reclaim is B unless the reclaim candle
// has the required directional displacement, in which case it is A. A
// marginal close is C and is deliberately ineligible to the strategies.
//
// This is intentionally detector-level rather than a projection of
// Pool.SweptAt/ReclaimedAt. Those fields are useful lifecycle facts, but the
// The detector examines every candle against every pool and can find a
// valid grab even when a pool was constructed before the current candle.
func GrabForBand(tf *analysiscontext.TimeframeContext, direction market.Direction, low, high, pipSize float64) *LiquidityGrab {
	if tf == nil {
		return nil
	}
	for i := len(tf.Liquidity.Pools) - 1; i >= 0; i-- {
		pool := tf.Liquidity.Pools[i]
		want := liquidity.LiquiditySellSide
		level := float64(pool.Low+pool.High) / 2
		if direction == market.Sell {
			want = liquidity.LiquidityBuySide
		}
		if pool.Side != want {
			continue
		}
		tolerance := math.Max(math.Abs(float64(pool.High-pool.Low))/2, math.Max(high-low, pipSize))
		if level < low-tolerance || level > high+tolerance {
			continue
		}
		for candleIndex := len(tf.Candles) - 1; candleIndex >= 0; candleIndex-- {
			candle := tf.Candles[candleIndex]
			tol := math.Max(candle.Range()*0.1, 0)
			swept := (pool.Side == liquidity.LiquidityBuySide && candle.High > float64(pool.High)) ||
				(pool.Side == liquidity.LiquiditySellSide && candle.Low < float64(pool.Low))
			if !swept {
				continue
			}
			clean := (direction == market.Buy && candle.Close > level) || (direction == market.Sell && candle.Close < level)
			marginal := (direction == market.Buy && candle.Close >= level-tol) || (direction == market.Sell && candle.Close <= level+tol)
			if !clean && !marginal {
				continue
			}
			grade := "C"
			if clean {
				grade = "B"
				if candle.Range() > 0 && candle.Body() >= .5*candle.Range() && directional(candle, direction) && hasReactionDisplacement(tf, candleIndex, direction, 3) {
					grade = "A"
				}
			}
			if grade == "C" {
				continue
			}
			return &LiquidityGrab{Pool: pool, Grade: grade}
		}
	}
	return nil
}

func directional(candle market.Candle, direction market.Direction) bool {
	return direction == market.Buy && candle.IsBullish() || direction == market.Sell && candle.IsBearish()
}

// hasReactionDisplacement applies the displacement leg contract at the strategy
// boundary. A displacement may start on the reclaim candle (the important
// same-bar case), or on one of the next three bars. Canonical structure
// displacement breaks are accepted as additional evidence; the candle-body
// test keeps the fallback causal and independent of a detector rerun.
func hasReactionDisplacement(tf *analysiscontext.TimeframeContext, index int, direction market.Direction, window int) bool {
	end := index + window
	if end >= len(tf.Candles) {
		end = len(tf.Candles) - 1
	}
	for i := index; i <= end; i++ {
		c := tf.Candles[i]
		if c.Range() > 0 && c.Body()/c.Range() >= .5 && directional(c, direction) {
			return true
		}
	}
	for _, brk := range tf.Structure.Breaks {
		if brk.Type != structure.BreakDisplacement || brk.Direction != direction {
			continue
		}
		for i := index; i <= end; i++ {
			if tf.Candles[i].Time == brk.Time || tf.Candles[i].Time == brk.ConfirmedAt {
				return true
			}
		}
	}
	return false
}

func StructuralDirection(ctx *analysiscontext.MarketContext) market.Direction {
	if ctx == nil {
		return ""
	}
	if ctx.Legacy != nil {
		return ctx.Legacy.Direction()
	}
	return ctx.Bias.Direction
}

func ChopEdgeAllows(tf *analysiscontext.TimeframeContext, direction market.Direction, low, high, edgeFraction float64) bool {
	if !InChop(tf) {
		return true
	}
	state := regimeOf(tf)
	height := state.RangeHigh - state.RangeLow
	if height <= legacyEpsilon {
		return false
	}
	edge := height * math.Max(0, math.Min(.5, edgeFraction))
	mid := (low + high) / 2
	if direction == market.Sell {
		return mid >= state.RangeHigh-edge-legacyEpsilon
	}
	return mid <= state.RangeLow+edge+legacyEpsilon
}

func distanceToBand(price, low, high float64) float64 {
	if price < low {
		return low - price
	}
	if price > high {
		return price - high
	}
	return 0
}

func candleAt(candles []market.Candle, at int64) *market.Candle {
	for i := len(candles) - 1; i >= 0; i-- {
		if candles[i].Time == at {
			return &candles[i]
		}
	}
	return nil
}
