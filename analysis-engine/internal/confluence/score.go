package confluence

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"

// Score is the combined-evidence result of overlapping independent
// Candidates at the same price. This is scoring composition only — it
// never decides whether the result is tradeable; that stays each
// contributing strategy's own StrategyQuality plus algo-bot's risk/policy
// layer, per docs/architecture/service-boundaries.md.
type Score struct {
	Overall    float64
	Contribute []opportunity.StrategyID
}
