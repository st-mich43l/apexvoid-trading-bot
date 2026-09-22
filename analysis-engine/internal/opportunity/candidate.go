// Package opportunity holds the technical-opportunity domain: a Candidate
// is not a TradePlan (see docs/architecture/algo-bot.md's "TradePlan
// ownership" — analysis-engine never publishes broker-ready position
// sizing). It is the pure, behavior-free result of a strategy's
// evaluation: what the setup is, where it is, why (evidence), and how
// good it looks by that strategy's own quality model.
//
// This package deliberately has NO dependency on internal/strategy — see
// docs/architecture/dependency-rules.md's "the one correction" for why:
// strategy.Strategy.Evaluate returns []opportunity.Candidate, so
// opportunity must sit below strategy in the dependency graph, not above
// it as the source architecture task's own §51 literally (and, per that
// same task's §21, inconsistently) orders it.
package opportunity

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// StrategyID identifies which strategy produced a Candidate. Defined here
// (not in internal/strategy) because both opportunity.Candidate and
// strategy.Strategy need it, and opportunity must not import strategy —
// internal/strategy aliases this as its own StrategyID for callers that
// only ever see the strategy package.
type StrategyID string

// EntryZone is a simple price range — deliberately not named "Zone" and
// deliberately not internal/zone's richer Zone type (supply/demand
// geometry with lifecycle/mitigation state, a higher layer opportunity
// must not depend on). See docs/architecture/analysis-engine.md's naming
// note on this exact point.
type EntryZone struct {
	Low  float64
	High float64
}

// Evidence is one machine-readable fact backing a Candidate — per the
// source task's §25, avoid opaque free-text-only explanations inside the
// core engine (e.g. "major_structure_bearish", "m5_bos_down",
// "liquidity_high_swept", "fvg_present", "retest_confirmed",
// "key_level_rejected"). Human-readable summaries are generated
// downstream, not stored here.
type Evidence struct {
	Code string
}

// Target is one take-profit thesis for a Candidate — a price plus why.
type Target struct {
	Price market.PriceLevel
}

// StrategyQuality is strategy-specific, not universal (§26): Overall is a
// single comparable score, Components is that strategy's own named
// dimensions (e.g. Breakout Retest: "breakout_quality", "retest_quality",
// "structure_quality", "location_quality" — a Liquidity Sweep strategy
// would use an entirely different set). No shared scoring formula is
// imposed here.
type StrategyQuality struct {
	Overall    float64
	Components map[string]float64
}

// Candidate is one strategy's technical opportunity, as of the source
// task's §24.
type Candidate struct {
	ID       string
	Strategy StrategyID
	Symbol   market.Symbol

	Direction market.Direction

	Entry        EntryZone
	Invalidation market.PriceLevel
	Targets      []Target

	Evidence []Evidence
	Quality  StrategyQuality

	CreatedAt int64
	ExpiresAt int64
}
