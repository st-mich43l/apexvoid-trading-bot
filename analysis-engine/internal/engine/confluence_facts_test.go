package engine

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/candle"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func TestFibTouchRetainsExactLevelProvenance(t *testing.T) {
	state := fib.State{Ladder: []fib.Level{{Ratio: .618, Price: 100, Kind: fib.KindRetracement}}}
	got := fibTouch(state, opportunity.Candidate{Entry: opportunity.EntryZone{Low: 99.9, High: 100.1}}, 2, .15)
	if got == nil || got.Ratio != .618 || got.Price != 100 || got.Kind != "retracement" {
		t.Fatalf("unexpected Fibonacci provenance: %+v", got)
	}
	if math.Abs(got.DistanceATR) > 1e-9 {
		t.Fatalf("unexpected ATR distance: %v", got.DistanceATR)
	}
}

func TestGradeAGrabRequiresReclaimedMatchingPool(t *testing.T) {
	swept := int64(10)
	z := zone.Zone{Side: zone.Demand, Low: 99, High: 100}
	candidate := opportunity.Candidate{
		CreatedAt: 10,
		Direction: market.Buy,
		Reaction:  &opportunity.ReactionConfirmation{ConfirmationBarTime: 10},
	}
	technical := &opportunity.TechnicalContext{CandleEvidence: &candle.Evidence{
		Rejection:    &candle.Rejection{Sweep: true, Reclaim: true},
		Displacement: &candle.Displacement{},
	}}
	pool := liquidity.Pool{
		ID: "sell-pool", Side: liquidity.LiquiditySellSide,
		Low: 98.9, High: 99.1, TouchCount: 3,
		SweptAt: &swept,
	}
	got := gradeAGrabForZone(z, candidate, technical, []liquidity.Pool{pool}, .1)
	if got == nil || got.PoolID != pool.ID || got.SweptAt != swept || got.ReclaimedAt != 10 {
		t.Fatalf("unexpected Grade-A provenance: %+v", got)
	}

	technical.CandleEvidence.Displacement = nil
	if got := gradeAGrabForZone(z, candidate, technical, []liquidity.Pool{pool}, .1); got != nil {
		t.Fatalf("sweep without displacement cannot receive Grade-A bonus: %+v", got)
	}
}

// A factor is set from a structured fact, never from the wording of an evidence code and never asserted: a code that
// says "rejection" is not a wick rejection, and the three sessions cover every hour so "a session is active" says nothing.
func TestFallbackFactorsComeFromStructuredFactsOnly(t *testing.T) {
	reaction := &opportunity.ReactionConfirmation{Pattern: "strong_reclaim", ReactionType: "rejection"}
	got := factsFromReaction(true, 2, false, reaction, false)
	if got.WickRejection || got.DisplacementGrade || got.SessionContext || !got.StructuralAgreement || !got.HTFAligned || got.Touches != 2 {
		t.Fatalf("a strong reclaim is structural evidence only: %+v", got)
	}
	if got := factsFromReaction(false, 0, true, nil, true); got.WickRejection || got.StructuralAgreement || got.SessionContext || !got.FibTouch || !got.CHoCH {
		t.Fatalf("without a reaction only the canonical facts remain: %+v", got)
	}
	if got := factsFromReaction(false, 0, false, &opportunity.ReactionConfirmation{Pattern: "rejection_choch"}, false); !got.CHoCH || !got.StructuralAgreement || got.WickRejection {
		t.Fatalf("a CHoCH confirmation carries the structure shift: %+v", got)
	}
}
