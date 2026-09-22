package engine

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/state"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// SnapshotVersion records which algorithm version produced this snapshot
// — source task §43: "a journal/replay must be able to identify which
// algorithm generated an opportunity."
type SnapshotVersion struct {
	StructureVersion string
	LiquidityVersion string
}

// AnalysisSnapshot is the one canonical, immutable analysis result model
// — source task §37/§44: what visualization, research, journal
// correlation, debugging, and replay all consume. Never the mutable
// state.SymbolState itself (its own doc comment says the same) — always
// a projection taken from it, via SnapshotFrom.
type AnalysisSnapshot struct {
	Symbol market.Symbol
	Time   int64

	Context context.MarketContext

	// Structure/Liquidity carry EVERY tracked timeframe's read, not just
	// the primary one Context.Structure/Context.Liquidity convenience-
	// expose — a renderer or research tool comparing timeframes needs all
	// of them (source task §44's own "chart rendering... strategy
	// research" use cases).
	Structure map[market.Timeframe]structure.StructureState
	Liquidity map[market.Timeframe]liquidity.LiquidityState

	Opportunities []opportunity.Candidate

	Version SnapshotVersion
}

// SnapshotFrom projects ws into an immutable AnalysisSnapshot as of now.
// Copies every map/slice — a caller holding a Snapshot must never see a
// later mutation of ws reflected in it.
func SnapshotFrom(ws *state.SymbolState, settings Settings, now int64) AnalysisSnapshot {
	structByTF := make(map[market.Timeframe]structure.StructureState, len(ws.Structure.ByTimeframe))
	for tf, s := range ws.Structure.ByTimeframe {
		structByTF[tf] = s
	}
	liqByTF := make(map[market.Timeframe]liquidity.LiquidityState, len(ws.Liquidity.ByTimeframe))
	for tf, l := range ws.Liquidity.ByTimeframe {
		liqByTF[tf] = l
	}
	var opps []opportunity.Candidate
	if ws.Opportunities != nil {
		opps = append(opps, ws.Opportunities.Candidates...)
	}
	return AnalysisSnapshot{
		Symbol: ws.Symbol, Time: now, Context: ws.Context,
		Structure: structByTF, Liquidity: liqByTF, Opportunities: opps,
		Version: SnapshotVersion{
			StructureVersion: settings.Structure.Version,
			LiquidityVersion: settings.Liquidity.Version,
		},
	}
}
