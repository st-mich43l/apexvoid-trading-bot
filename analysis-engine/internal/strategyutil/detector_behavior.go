package strategyutil

// This file contains the small, shared pieces of the frozen Python detector
// contract used by more than one strategy.  It intentionally consumes the
// canonical MarketContext; it never rebuilds structure, zones or liquidity.

import (
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
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
