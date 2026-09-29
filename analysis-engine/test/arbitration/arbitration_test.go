package arbitration_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/arbitration"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func candidate(id string, direction market.Direction, quality float64) opportunity.Candidate {
	return opportunity.Candidate{
		ID: id, Strategy: "key_level", StrategyVersion: "v1", Symbol: "USDJPY", Direction: direction,
		Entry:        opportunity.EntryZone{Low: 157.0, High: 157.1},
		Invalidation: market.PriceLevel{Price: 156.9, Label: "invalidated"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 157.3, Label: "opposing_liquidity"}}},
		Evidence:     []opportunity.Evidence{{Code: "m5_key_level_reaction"}},
		Quality:      opportunity.StrategyQuality{Overall: quality},
		CreatedAt:    10, ExpiresAt: 30,
		Provenance: opportunity.AnalysisProvenance{StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: "fixture"},
	}
}

func decisionFor(decisions []arbitration.Decision, id string) arbitration.Decision {
	for _, d := range decisions {
		if d.CandidateID == id {
			return d
		}
	}
	return arbitration.Decision{}
}

func TestArbitrate_EmptyLiveSetReturnsNoDecisions(t *testing.T) {
	decisions := arbitration.Arbitrate(nil, arbitration.Config{ConflictMarginQuality: 0.15})
	if decisions != nil {
		t.Fatalf("expected nil decisions for an empty live set, got %v", decisions)
	}
}

func TestArbitrate_SingleCandidateIsUncontested(t *testing.T) {
	live := []opportunity.Candidate{candidate("a", market.Buy, 0.5)}
	decisions := arbitration.Arbitrate(live, arbitration.Config{ConflictMarginQuality: 0.15})
	if len(decisions) != 1 {
		t.Fatalf("expected exactly one decision, got %d", len(decisions))
	}
	if decisions[0].Status != arbitration.StatusUncontested {
		t.Fatalf("expected uncontested, got %s", decisions[0].Status)
	}
	if decisions[0].ReasonCode != arbitration.ReasonUncontested {
		t.Fatalf("expected reason %s, got %s", arbitration.ReasonUncontested, decisions[0].ReasonCode)
	}
}

func TestArbitrate_SameDirectionRanksByQualityOverall(t *testing.T) {
	live := []opportunity.Candidate{
		candidate("low", market.Buy, 0.60),
		candidate("high", market.Buy, 0.90),
		candidate("mid", market.Buy, 0.75),
	}
	decisions := arbitration.Arbitrate(live, arbitration.Config{ConflictMarginQuality: 0.15})

	if got := decisionFor(decisions, "high").Status; got != arbitration.StatusWinner {
		t.Fatalf("expected the highest-quality same-direction candidate to win, got %s", got)
	}
	for _, id := range []string{"low", "mid"} {
		if got := decisionFor(decisions, id).Status; got != arbitration.StatusSuppressed {
			t.Fatalf("expected %s to be suppressed as a lower-ranked same-direction also-ran, got %s", id, got)
		}
	}
}

func TestArbitrate_DecisiveOppositeDirectionResolvesToTheStrongerSide(t *testing.T) {
	live := []opportunity.Candidate{
		candidate("buy", market.Buy, 0.90),
		candidate("sell", market.Sell, 0.50),
	}
	decisions := arbitration.Arbitrate(live, arbitration.Config{ConflictMarginQuality: 0.15})

	if got := decisionFor(decisions, "buy").Status; got != arbitration.StatusWinner {
		t.Fatalf("expected the decisively higher-quality candidate to win, got %s", got)
	}
	if got := decisionFor(decisions, "sell").Status; got != arbitration.StatusSuppressed {
		t.Fatalf("expected the losing direction to be suppressed, got %s", got)
	}
	for _, d := range decisions {
		if d.ReasonCode != arbitration.ReasonRankedSingleDirection {
			t.Fatalf("expected reason %s, got %s", arbitration.ReasonRankedSingleDirection, d.ReasonCode)
		}
	}
}

func TestArbitrate_OppositeDirectionWithinMarginHoldsForBothSides(t *testing.T) {
	live := []opportunity.Candidate{
		candidate("buy", market.Buy, 0.80),
		candidate("sell", market.Sell, 0.75),
	}
	decisions := arbitration.Arbitrate(live, arbitration.Config{ConflictMarginQuality: 0.15})

	for _, d := range decisions {
		if d.Status != arbitration.StatusConflictHeld {
			t.Fatalf("expected every candidate held on a near-tie, got %s for %s", d.Status, d.CandidateID)
		}
		if d.ReasonCode != arbitration.ReasonOppositeDirectionHeld {
			t.Fatalf("expected reason %s, got %s", arbitration.ReasonOppositeDirectionHeld, d.ReasonCode)
		}
	}
	if got := decisionFor(decisions, "buy").ConflictingWith; len(got) != 2 {
		t.Fatalf("expected ConflictingWith to name both sides, got %v", got)
	}
}

func TestArbitrate_MarginBoundaryIsInclusive(t *testing.T) {
	// Exactly at the margin must be decisive (>=), not held.
	live := []opportunity.Candidate{
		candidate("buy", market.Buy, 0.80),
		candidate("sell", market.Sell, 0.65),
	}
	decisions := arbitration.Arbitrate(live, arbitration.Config{ConflictMarginQuality: 0.15})
	if got := decisionFor(decisions, "buy").Status; got != arbitration.StatusWinner {
		t.Fatalf("expected a gap exactly at the margin to be decisive, got %s", got)
	}
}

func TestArbitrate_TiesBreakDeterministicallyOnCandidateID(t *testing.T) {
	live := []opportunity.Candidate{
		candidate("bbb", market.Buy, 0.80),
		candidate("aaa", market.Buy, 0.80),
	}
	first := arbitration.Arbitrate(live, arbitration.Config{ConflictMarginQuality: 0.15})
	// Re-run with the slice reversed - the result must be identical
	// regardless of input order (Arbitrate sorts internally).
	reversed := []opportunity.Candidate{live[1], live[0]}
	second := arbitration.Arbitrate(reversed, arbitration.Config{ConflictMarginQuality: 0.15})

	if decisionFor(first, "aaa").Status != arbitration.StatusWinner {
		t.Fatalf("expected the lexicographically-smaller ID to win an exact quality tie")
	}
	if decisionFor(first, "aaa").Status != decisionFor(second, "aaa").Status {
		t.Fatalf("expected Arbitrate to be deterministic regardless of input order")
	}
}

func TestArbitrate_ManyCandidatesGridlockOnNearTieMatchesProductionShape(t *testing.T) {
	// Mirrors the real production observation this package was built to
	// fix: many same-tier-strength candidates split across both
	// directions, none decisively ahead.
	live := make([]opportunity.Candidate, 0, 10)
	for i := 0; i < 5; i++ {
		live = append(live, candidate(string(rune('a'+i)), market.Buy, 0.70))
		live = append(live, candidate(string(rune('A'+i)), market.Sell, 0.68))
	}
	decisions := arbitration.Arbitrate(live, arbitration.Config{ConflictMarginQuality: 0.15})
	for _, d := range decisions {
		if d.Status != arbitration.StatusConflictHeld {
			t.Fatalf("expected the whole gridlocked set held, got %s for %s", d.Status, d.CandidateID)
		}
	}
}
