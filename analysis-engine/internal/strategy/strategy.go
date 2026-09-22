// Package strategy defines the shared strategy engineering contract — not
// shared trading behavior. See docs/adr/003-independent-strategy-model.md:
// the legacy generic "reaction" family (supply/demand/key_level/
// trendline/liquidity sharing entry/confirmation/invalidation/quality/
// targeting/expiry logic) is rejected. Every strategy under
// internal/strategy/<name>/ owns its full thesis; this package holds only
// the interface the engine calls it through.
//
// No strategy implementation lives here or under this package directly —
// per the source architecture task's own §58 ("do not create hundreds of
// empty .go files") and this task's own restraint principle, the
// per-strategy subpackages named in the source task's §20
// (breakoutretest, liquiditysweep, orderblock, fvg, keylevel, supply,
// demand, trendline, sessionlevel, rangeedge, boxbreakout,
// impulsepullback, rangesweep, snapback) are architectural slots, not
// directories created by this task — see
// docs/architecture/analysis-engine.md's "Independent strategy
// architecture" section. They are created once the market-structure
// specification (the next task after this one) is approved and a
// strategy is actually ported or written.
package strategy

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// StrategyID re-exports opportunity.StrategyID so callers that only ever
// see this package don't need to import opportunity directly for the ID
// type alone. opportunity remains the one place StrategyID is defined —
// see opportunity/candidate.go's doc comment.
type StrategyID = opportunity.StrategyID

// Strategy is the engineering contract every strategy implements, per the
// source task's §21. Do NOT put generic implementations of entry,
// confirmation, invalidation, quality scoring, targeting, or expiry into
// this package or any shared base unless the behavior is genuinely
// universal (§21) — see ADR-003.
type Strategy interface {
	ID() StrategyID
	RequiredTimeframes() []market.Timeframe
	Evaluate(ctx *context.MarketContext) []opportunity.Candidate
}
