package strategyutil

// This file contains the small, shared pieces of the frozen Python detector
// contract used by more than one strategy.  It intentionally consumes the
// canonical MarketContext; it never rebuilds structure, zones or liquidity.

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
)

type LiquidityGrab struct {
	Pool  liquidity.Pool
	Grade string
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

func StructuralDirection(ctx *analysiscontext.MarketContext) market.Direction {
	if ctx == nil {
		return ""
	}
	if ctx.Legacy != nil {
		return ctx.Legacy.Direction()
	}
	return ctx.Bias.Direction
}
