// Package context builds the canonical combined market interpretation —
// MarketContext — that strategies read and must not rebuild (source task
// §28/§29: "BreakoutRetest -> recalculate ATR -> recalculate swings ->
// rebuild zones" is forbidden; "BreakoutRetest -> read
// SymbolState/MarketContext" is required).
//
// Depends on internal/structure, internal/liquidity, internal/zone,
// internal/session, internal/fib, internal/keylevel, and internal/trendline
// (all real as of Analysis Engine V2)
// — context sits above every one of them in docs/architecture/
// dependency-rules.md's amended rank table.
package context

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
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
	Zones      ZoneContext
	Trendline  TrendlineContext
	KeyLevel   KeyLevelContext
	Fib        FibContext
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
	Zones     zone.ZoneState
	Trendline trendline.TrendlineState
	KeyLevel  keylevel.State
	Session   session.State
	Fib       fib.State
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

// ZoneContext is the primary-timeframe convenience view. Timeframes keeps
// every timeframe's zone state so strategies can preserve disagreement
// between HTF and execution-timeframe zones.
type ZoneContext struct {
	Timeframe market.Timeframe
	State     zone.ZoneState
}

// TrendlineContext mirrors ZoneContext for the trendline domain.
type TrendlineContext struct {
	Timeframe market.Timeframe
	State     trendline.TrendlineState
}

// KeyLevelContext mirrors ZoneContext for the key-level domain.
type KeyLevelContext struct {
	Timeframe market.Timeframe
	State     keylevel.State
}

// FibContext mirrors ZoneContext for the fib domain (ladder + dealing
// range).
type FibContext struct {
	Timeframe market.Timeframe
	State     fib.State
}

// BiasContext is DERIVED from Structure — source task §36: "bias should
// consume canonical structure... do not write another parallel HTF-bias
// swing algorithm." See DeriveBias.
type BiasContext struct {
	Direction market.Direction
	Layer     structure.StructureLayer
	Trend     structure.TrendState
}

// RegimeContext / VolatilityContext: minimal, honest placeholders. Regime
// (source task §38) classification is NOT implemented this task — it
// does not appear in this task's own Definition of Done (§74's 50
// items) — and is recorded as not-yet-implemented in
// docs/analysis-engine-v2-migration.md rather than silently stubbed with
// fabricated logic. Volatility carries the one real value already
// available at this layer (the canonical ATR this MarketContext's
// structure/liquidity passes were built from).
//
// SessionContext (source task §39) was the same kind of honest
// placeholder until Phase S4's session domain — it now carries the real
// session.State for the primary timeframe (see Build).
type RegimeContext struct {
	Kind string // empty until §38 is implemented
}

type VolatilityContext struct {
	ATR float64
}

type SessionContext struct {
	Timeframe market.Timeframe
	State     session.State
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
			Timeframe: tf, Structure: input.Structure, Liquidity: input.Liquidity, Zones: input.Zones,
			Trendline: input.Trendline, KeyLevel: input.KeyLevel, Session: input.Session, Fib: input.Fib,
		}
	}

	ctx := MarketContext{Symbol: symbol, Timeframes: timeframes}
	if primaryInput, ok := perTimeframe[primary]; ok {
		ctx.Structure = StructureContext{Timeframe: primary, State: primaryInput.Structure}
		ctx.Liquidity = LiquidityContext{Timeframe: primary, State: primaryInput.Liquidity}
		ctx.Zones = ZoneContext{Timeframe: primary, State: primaryInput.Zones}
		ctx.Trendline = TrendlineContext{Timeframe: primary, State: primaryInput.Trendline}
		ctx.KeyLevel = KeyLevelContext{Timeframe: primary, State: primaryInput.KeyLevel}
		ctx.Fib = FibContext{Timeframe: primary, State: primaryInput.Fib}
		ctx.Bias = DeriveBias(primaryInput.Structure)
		ctx.Volatility = VolatilityContext{ATR: primaryInput.ATR}
		ctx.Session = SessionContext{Timeframe: primary, State: primaryInput.Session}
	}
	return ctx
}

// TimeframeInput is one timeframe's already-computed analytical output,
// the input Build combines — never recomputed by context itself.
type TimeframeInput struct {
	Structure structure.StructureState
	Liquidity liquidity.LiquidityState
	Zones     zone.ZoneState
	Trendline trendline.TrendlineState
	KeyLevel  keylevel.State
	Session   session.State
	Fib       fib.State
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
