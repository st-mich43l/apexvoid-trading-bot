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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
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

// Mean reference sources.
const (
	MeanRangeEquilibrium   = "range_equilibrium"
	MeanDealingEquilibrium = "dealing_equilibrium"
)

// MeanReference is the price a short-term reversion is measured against and aimed at: the
// equilibrium of the established local range when the frame has one that has not been
// broken (the range builder's own state), otherwise the equilibrium of the dealing range.
// ok is false when the frame has neither.
func MeanReference(tf *analysiscontext.TimeframeContext) (price float64, source string, ok bool) {
	if tf != nil && tf.Legacy != nil && tf.Legacy.ScalpRange != nil {
		switch r := tf.Legacy.ScalpRange; r.State {
		case techniquezone.RangeStateConfirmed, techniquezone.RangeStatePostImpulse, techniquezone.RangeStateProvisional:
			if r.Equilibrium > 0 {
				return r.Equilibrium, MeanRangeEquilibrium, true
			}
		}
	}
	if dealing := dealingRange(tf); dealing != nil && float64(dealing.Equilibrium) > 0 {
		return float64(dealing.Equilibrium), MeanDealingEquilibrium, true
	}
	return 0, "", false
}
