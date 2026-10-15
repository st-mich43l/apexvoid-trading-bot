package arbitration_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/arbitration"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

var ignoreQuality = arbitration.Config{ConflictMarginQuality: 0.15, IgnoreQuality: true}

func TestArbitrate_IgnoreQualityOrdersByIDNotByScore(t *testing.T) {
	live := []opportunity.Candidate{
		candidate("a-weak", market.Buy, 0.10),
		candidate("b-strong", market.Buy, 0.99),
	}
	decisions := arbitration.Arbitrate(live, ignoreQuality)

	if got := decisionFor(decisions, "a-weak").Status; got != arbitration.StatusWinner {
		t.Fatalf("expected the lowest id to win when quality is ignored, got %s", got)
	}
	if got := decisionFor(decisions, "b-strong").Status; got != arbitration.StatusSuppressed {
		t.Fatalf("expected the higher score to lose nothing it never had, got %s", got)
	}
}

func TestArbitrate_IgnoreQualityHoldsADecisiveQualityGapWithoutBias(t *testing.T) {
	live := []opportunity.Candidate{
		candidate("buy", market.Buy, 0.95),
		candidate("sell", market.Sell, 0.20),
	}
	if got := decisionFor(arbitration.Arbitrate(live, arbitration.Config{ConflictMarginQuality: 0.15}), "buy").Status; got != arbitration.StatusWinner {
		t.Fatalf("control: quality still decides by default, got %s", got)
	}
	decisions := arbitration.Arbitrate(live, ignoreQuality)
	for _, id := range []string{"buy", "sell"} {
		d := decisionFor(decisions, id)
		if d.Status != arbitration.StatusConflictHeld || d.ReasonCode != arbitration.ReasonOppositeDirectionHeld {
			t.Fatalf("expected %s held as an opposite-direction conflict, got %s/%s", id, d.Status, d.ReasonCode)
		}
	}
}

func TestArbitrate_IgnoreQualityIsInvariantToEveryScore(t *testing.T) {
	build := func(buy, sell float64) []opportunity.Candidate {
		return []opportunity.Candidate{
			candidate("b1", market.Buy, buy), candidate("b2", market.Buy, sell),
			candidate("s1", market.Sell, sell),
		}
	}
	first := arbitration.Arbitrate(build(0.9, 0.1), ignoreQuality)
	second := arbitration.Arbitrate(build(0.1, 0.9), ignoreQuality)
	if len(first) != len(second) {
		t.Fatalf("decision counts differ: %d vs %d", len(first), len(second))
	}
	for i := range first {
		if first[i].CandidateID != second[i].CandidateID || first[i].Status != second[i].Status || first[i].ReasonCode != second[i].ReasonCode {
			t.Fatalf("scores changed the decision for %s: %+v vs %+v", first[i].CandidateID, first[i], second[i])
		}
	}
}
