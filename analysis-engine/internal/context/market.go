// Package context builds the canonical combined market interpretation —
// MarketContext — that strategies read and must not rebuild (source task
// §28/§29: "BreakoutRetest -> recalculate ATR -> recalculate swings ->
// rebuild zones" is forbidden; "BreakoutRetest -> read
// SymbolState/MarketContext" is required).
//
// Depends on internal/structure and internal/liquidity (both real as of
// Analysis Engine V2) — context sits above both in
// docs/architecture/dependency-rules.md's amended rank table.
package context

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// MarketContext is the canonical combined interpretation for one symbol —
// source task §35. Structure/Liquidity/Bias are the PRIMARY timeframe's
// view (config-selected — the timeframe strategies execute against, e.g.
// M5), a convenience so a strategy that only cares about "the" current
// read doesn't have to index into Timeframes itself; Timeframes holds
// every tracked timeframe's own full read, preserving disagreement
// between them rather than flattening it (source task §37: "H1 bearish,
// M15 bearish, M5 bullish internal pullback, M1 bullish micro... do NOT
// flatten this immediately into bias = neutral").
type MarketContext struct {
	Symbol market.Symbol

	Timeframes map[market.Timeframe]*TimeframeContext

	Structure  StructureContext
	Liquidity  LiquidityContext
	Zones      ZoneContext // placeholder — internal/zone is not implemented this task, see docs/analysis-engine-v2-migration.md
	Bias       BiasContext
	Regime     RegimeContext
	Volatility VolatilityContext
	Session    SessionContext
}

// TimeframeContext is one timeframe's full structural+liquidity read —
// the unit source task §37's multi-timeframe disagreement is built from.
type TimeframeContext struct {
	Timeframe market.Timeframe
	Structure structure.StructureState
	Liquidity liquidity.LiquidityState
}

// StructureContext is MarketContext's primary-timeframe structural view —
// a direct reference to that timeframe's TimeframeContext.Structure, never
// an independent recomputation.
type StructureContext struct {
	Timeframe market.Timeframe
	State     structure.StructureState
}

// LiquidityContext mirrors StructureContext for the liquidity domain.
type LiquidityContext struct {
	Timeframe market.Timeframe
	State     liquidity.LiquidityState
}

// ZoneContext is a placeholder — internal/zone (supply/demand/OB/FVG) is
// explicitly out of scope for this task's Definition of Done (source task
// §32: "only after Structure V2 is stable"). Not implemented; not
// silently dropped either — see docs/analysis-engine-v2-migration.md.
type ZoneContext struct{}

// BiasContext is DERIVED from Structure — source task §36: "bias should
// consume canonical structure... do not write another parallel HTF-bias
// swing algorithm." See DeriveBias.
type BiasContext struct {
	Direction market.Direction
	Layer     structure.StructureLayer
	Trend     structure.TrendState
}

// RegimeContext / VolatilityContext / SessionContext: minimal, honest
// placeholders. Regime (source task §38) and Session (§39) classification
// are NOT implemented this task — neither appears in this task's own
// Definition of Done (§74's 50 items) — and are recorded as not-yet-
// implemented in docs/analysis-engine-v2-migration.md rather than
// silently stubbed with fabricated logic. Volatility carries the one real
// value already available at this layer (the canonical ATR this
// MarketContext's structure/liquidity passes were built from).
type RegimeContext struct {
	Kind string // empty until §38 is implemented
}

type VolatilityContext struct {
	ATR float64
}

type SessionContext struct {
	Name string // empty until §39 is implemented
}

// Build assembles a MarketContext from a per-timeframe map of already-
// computed (structure.StructureState, liquidity.LiquidityState, ATR)
// triples — the composition-layer entrypoint (internal/engine calls this
// once it has run structure.Update/liquidity.Update per timeframe; Build
// itself computes nothing analytical, only combines). primary selects
// which timeframe's read populates the convenience Structure/Liquidity/
// Bias fields.
func Build(
	symbol market.Symbol,
	primary market.Timeframe,
	perTimeframe map[market.Timeframe]TimeframeInput,
) MarketContext {
	timeframes := make(map[market.Timeframe]*TimeframeContext, len(perTimeframe))
	for tf, input := range perTimeframe {
		timeframes[tf] = &TimeframeContext{
			Timeframe: tf, Structure: input.Structure, Liquidity: input.Liquidity,
		}
	}

	ctx := MarketContext{Symbol: symbol, Timeframes: timeframes}
	if primaryInput, ok := perTimeframe[primary]; ok {
		ctx.Structure = StructureContext{Timeframe: primary, State: primaryInput.Structure}
		ctx.Liquidity = LiquidityContext{Timeframe: primary, State: primaryInput.Liquidity}
		ctx.Bias = DeriveBias(primaryInput.Structure)
		ctx.Volatility = VolatilityContext{ATR: primaryInput.ATR}
	}
	return ctx
}

// TimeframeInput is one timeframe's already-computed analytical output,
// the input Build combines — never recomputed by context itself.
type TimeframeInput struct {
	Structure structure.StructureState
	Liquidity liquidity.LiquidityState
	ATR       float64
}

// DeriveBias reads Structure's own layer trends, preferring the highest
// layer with a definitive (non-Range/Unknown) read — Major first, falling
// through to Intermediate/Internal/Micro — source task §36. Never
// computes anything structure.Update did not already compute.
func DeriveBias(state structure.StructureState) BiasContext {
	candidates := []structure.LayerState{state.Major, state.Intermediate, state.Internal, state.Micro}
	for _, layer := range candidates {
		switch layer.Trend {
		case structure.TrendBullish:
			return BiasContext{Direction: market.Buy, Layer: layer.Layer, Trend: layer.Trend}
		case structure.TrendBearish:
			return BiasContext{Direction: market.Sell, Layer: layer.Layer, Trend: layer.Trend}
		}
	}
	return BiasContext{Trend: structure.TrendUnknown}
}
