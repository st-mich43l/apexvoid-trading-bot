// Package context builds the canonical combined market interpretation —
// MarketContext — that strategies read and must not rebuild (§28/§29 of
// the source architecture task: "BreakoutRetest -> recalculate ATR ->
// recalculate swings -> rebuild zones" is forbidden; "BreakoutRetest ->
// read SymbolState/MarketContext" is required).
//
// See docs/architecture/analysis-engine.md for the frozen shape this file
// implements. Sub-context fields below are minimal placeholders proving
// the dependency edges (context -> market; state -> context) that
// docs/architecture/dependency-rules.md requires be real, not just
// described — filling them with real bias/regime/liquidity/volatility/
// session computation is Stage 3+ work (docs/go-analysis-migration-audit.md),
// not this architecture task.
package context

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// MarketContext is the canonical combined interpretation for one symbol,
// as of the source task's §19.
type MarketContext struct {
	Symbol market.Symbol

	Timeframes map[market.Timeframe]*TimeframeContext

	Bias       BiasContext
	Regime     RegimeContext
	Liquidity  LiquidityContext
	Volatility VolatilityContext
	Session    SessionContext
}

// TimeframeContext holds the per-timeframe view of MarketContext —
// dealing range, structure hierarchy, MTF alignment. Placeholder; ports
// dealing_range.py, structure hierarchy, and the MTF context glue
// (context/{timeframe,dealing_range,mtf}.go in the proposed tree).
type TimeframeContext struct {
	Timeframe market.Timeframe
}

// BiasContext is the resolved directional bias for a symbol (ports
// app/analysis's HTF-bias resolution, e.g. structural_reaction_support's
// bias_relationship — see this session's PR #575, which taught the
// premium/discount gate to read this exact relationship).
type BiasContext struct {
	Direction market.Direction
}

// RegimeContext ports regime.py (accepted_box_break / displacement_grade).
type RegimeContext struct {
	Kind string // e.g. "trend", "range" — enum once regime.go is ported
}

// LiquidityContext is MarketContext's view into internal/liquidity's
// Book for this symbol (a projection, not a duplicate — the raw Book
// lives on internal/state.SymbolState).
type LiquidityContext struct{}

// VolatilityContext ports the ATR/volatility view a strategy reads
// (internal/indicator's output, contextualized per timeframe).
type VolatilityContext struct {
	ATR float64
}

// SessionContext ports session_liquidity.py / session context
// (London/NY/Asia window classification).
type SessionContext struct {
	Name string
}
