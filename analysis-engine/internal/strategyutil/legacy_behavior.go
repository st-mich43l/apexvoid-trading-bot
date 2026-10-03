package strategyutil

// This file contains the small, shared pieces of the frozen Python detector
// contract used by more than one strategy.  It intentionally consumes the
// canonical MarketContext; it never rebuilds structure, zones or liquidity.

import (
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

type LiquidityGrab struct {
	Pool  liquidity.Pool
	Grade string
}

func PremiumDiscountAllows(tf *analysiscontext.TimeframeContext, direction market.Direction, strict bool) bool {
	if tf == nil || tf.Fib.Range == nil {
		return true
	}
	if tf.Fib.Range.Zone == "eq" {
		return false
	}
	if direction == market.Buy {
		if strict {
			return tf.Fib.Range.Zone == "discount"
		}
		return tf.Fib.Range.Zone != "premium"
	}
	if strict {
		return tf.Fib.Range.Zone == "premium"
	}
	return tf.Fib.Range.Zone != "discount"
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

// GrabForBand maps Go's canonical swept/reclaimed pools onto the Python A/B
// grab contract.  A requires a directional displacement body on the reclaim
// bar; a clean reclaim without displacement is B.  An unreclaimed/marginal
// sweep is deliberately not returned (Python grade C was ineligible).
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
		if pool.Side != want || pool.SweptAt == nil || pool.ReclaimedAt == nil {
			continue
		}
		tolerance := math.Max(math.Abs(float64(pool.High-pool.Low))/2, math.Max(high-low, pipSize))
		if level < low-tolerance || level > high+tolerance {
			continue
		}
		grade := "B"
		if reclaim := candleAt(tf.Candles, *pool.ReclaimedAt); reclaim != nil && reclaim.Range() > 0 && reclaim.Body() >= .5*reclaim.Range() && ((direction == market.Buy && reclaim.IsBullish()) || (direction == market.Sell && reclaim.IsBearish())) {
			grade = "A"
		}
		return &LiquidityGrab{Pool: pool, Grade: grade}
	}
	return nil
}

func StructuralDirection(ctx *analysiscontext.MarketContext) market.Direction {
	if ctx == nil {
		return ""
	}
	return ctx.Bias.Direction
}

func ChopEdgeAllows(tf *analysiscontext.TimeframeContext, direction market.Direction, low, high, edgeFraction float64) bool {
	if tf == nil || tf.Regime.Kind != "chop" {
		return true
	}
	height := tf.Regime.RangeHigh - tf.Regime.RangeLow
	if height <= 0 {
		return false
	}
	edgeFraction = math.Max(0, math.Min(.5, edgeFraction))
	mid := (low + high) / 2
	if direction == market.Buy {
		return mid <= tf.Regime.RangeLow+height*edgeFraction
	}
	return mid >= tf.Regime.RangeHigh-height*edgeFraction
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
