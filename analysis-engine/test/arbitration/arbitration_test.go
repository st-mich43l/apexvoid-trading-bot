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

func withStructuralID(c opportunity.Candidate, structuralID string) opportunity.Candidate {
	c.StructuralID = structuralID
	return c
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

func TestArbitrate_SameThesisCandidatesShareOneOutcome(t *testing.T) {
	// Two observations of the SAME real-world zone (e.g. supply's resting
	// vs. confirmed reaction) - different strategy-assigned IDs, same
	// Direction + StructuralID. Must never compete against each other, and
	// must share the group's outcome.
	resting := withStructuralID(candidate("resting", market.Buy, 0.60), "zone-42")
	confirmed := withStructuralID(candidate("confirmed", market.Buy, 0.90), "zone-42")
	rival := withStructuralID(candidate("rival", market.Sell, 0.20), "zone-99")

	decisions := arbitration.Arbitrate(
		[]opportunity.Candidate{resting, confirmed, rival},
		arbitration.Config{ConflictMarginQuality: 0.15},
	)

	restingD, confirmedD, rivalD := decisionFor(decisions, "resting"), decisionFor(decisions, "confirmed"), decisionFor(decisions, "rival")
	if restingD.Status != arbitration.StatusWinner || confirmedD.Status != arbitration.StatusWinner {
		t.Fatalf("expected both same-thesis observations to win together, got resting=%s confirmed=%s", restingD.Status, confirmedD.Status)
	}
	if rivalD.Status != arbitration.StatusSuppressed {
		t.Fatalf("expected the weaker rival thesis suppressed, got %s", rivalD.Status)
	}
	// The representative is whichever ranks best within the group
	// (highest quality, i.e. "confirmed" at 0.90) - both members point to
	// it as ThesisID and name each other in MergedWith.
	if restingD.ThesisID != "confirmed" || confirmedD.ThesisID != "confirmed" {
		t.Fatalf("expected both to share ThesisID=confirmed, got resting=%q confirmed=%q", restingD.ThesisID, confirmedD.ThesisID)
	}
	if len(restingD.MergedWith) != 1 || restingD.MergedWith[0] != "confirmed" {
		t.Fatalf("expected resting.MergedWith=[confirmed], got %v", restingD.MergedWith)
	}
	if len(confirmedD.MergedWith) != 1 || confirmedD.MergedWith[0] != "resting" {
		t.Fatalf("expected confirmed.MergedWith=[resting], got %v", confirmedD.MergedWith)
	}
	if rivalD.ThesisID != "" || rivalD.MergedWith != nil {
		t.Fatalf("expected the unrelated rival to have no thesis correlation, got ThesisID=%q MergedWith=%v", rivalD.ThesisID, rivalD.MergedWith)
	}
}

func TestArbitrate_SameThesisDuplicatesDoNotInflateRankingAgainstARival(t *testing.T) {
	// Three observations of the SAME zone, each individually weaker than a
	// single opposing rival - if thesis grouping did not happen before
	// ranking, three same-direction entries might look like a "crowd"
	// argument for that direction. It must not: it is one thesis, still
	// weaker than the rival, so the rival must win outright, not hold.
	a := withStructuralID(candidate("a", market.Buy, 0.50), "zone-1")
	b := withStructuralID(candidate("b", market.Buy, 0.52), "zone-1")
	c := withStructuralID(candidate("c", market.Buy, 0.48), "zone-1")
	rival := withStructuralID(candidate("rival", market.Sell, 0.90), "zone-2")

	decisions := arbitration.Arbitrate(
		[]opportunity.Candidate{a, b, c, rival},
		arbitration.Config{ConflictMarginQuality: 0.15},
	)

	if decisionFor(decisions, "rival").Status != arbitration.StatusWinner {
		t.Fatalf("expected the decisively stronger rival thesis to win outright")
	}
	for _, id := range []string{"a", "b", "c"} {
		if decisionFor(decisions, id).Status != arbitration.StatusSuppressed {
			t.Fatalf("expected %s suppressed as part of the losing thesis", id)
		}
		if decisionFor(decisions, id).ThesisID != "b" { // b has the highest quality (0.52) among a/b/c
			t.Fatalf("expected %s to point at the group's representative b, got %q", id, decisionFor(decisions, id).ThesisID)
		}
	}
}

func TestArbitrate_DifferentDirectionsWithTheSameStructuralIDDoNotMerge(t *testing.T) {
	// Same StructuralID but opposite Direction cannot be the same trade
	// thesis by construction (thesisKey includes Direction).
	buy := withStructuralID(candidate("buy", market.Buy, 0.80), "zone-7")
	sell := withStructuralID(candidate("sell", market.Sell, 0.10), "zone-7")

	decisions := arbitration.Arbitrate(
		[]opportunity.Candidate{buy, sell},
		arbitration.Config{ConflictMarginQuality: 0.15},
	)

	if decisionFor(decisions, "buy").ThesisID != "" || decisionFor(decisions, "sell").ThesisID != "" {
		t.Fatalf("opposite-direction candidates must never share a ThesisID even with the same StructuralID")
	}
	if decisionFor(decisions, "buy").Status != arbitration.StatusWinner {
		t.Fatalf("expected the decisively stronger side to win")
	}
}

func TestArbitrate_EmptyStructuralIDNeverMerges(t *testing.T) {
	// Two candidates with no StructuralID at all (a strategy with no
	// persistent identity to offer) must never be treated as the same
	// thesis just because both happen to be empty strings.
	a := candidate("a", market.Buy, 0.50)
	b := candidate("b", market.Buy, 0.50)

	decisions := arbitration.Arbitrate([]opportunity.Candidate{a, b}, arbitration.Config{ConflictMarginQuality: 0.15})

	if decisionFor(decisions, "a").ThesisID != "" || decisionFor(decisions, "b").ThesisID != "" {
		t.Fatalf("expected no thesis correlation when StructuralID is empty")
	}
}

func inPlayCandidate(id string, direction market.Direction, quality, low, high float64) opportunity.Candidate {
	c := candidate(id, direction, quality)
	c.Entry = opportunity.EntryZone{Low: low, High: high}
	c.Technical = &opportunity.TechnicalContext{ATR: 1.0}
	return c
}

func TestArbitrateInPlay_FarOppositeZoneDoesNotHoldTheSymbol(t *testing.T) {
	cfg := arbitration.Config{ConflictMarginQuality: 0.15, InPlayATR: 1.0}
	live := []opportunity.Candidate{
		inPlayCandidate("demand_here", market.Buy, 0.70, 100.0, 100.5),
		inPlayCandidate("supply_far", market.Sell, 0.72, 120.0, 120.5), // near-tied quality, but 19 ATR away
	}

	decisions := arbitration.ArbitrateInPlay(live, cfg, 100.2)

	if got := decisionFor(decisions, "demand_here"); got.Status != arbitration.StatusUncontested {
		t.Fatalf("the only in-play candidate must be uncontested, got %s/%s", got.Status, got.ReasonCode)
	}
	far := decisionFor(decisions, "supply_far")
	if far.Status != arbitration.StatusSuppressed || far.ReasonCode != arbitration.ReasonNotInPlay {
		t.Fatalf("a zone price is nowhere near must be suppressed as not_in_play, got %s/%s", far.Status, far.ReasonCode)
	}
}

func TestArbitrateInPlay_BothSidesInPlayStillHoldOnANearTie(t *testing.T) {
	cfg := arbitration.Config{ConflictMarginQuality: 0.15, InPlayATR: 1.0}
	live := []opportunity.Candidate{
		inPlayCandidate("demand", market.Buy, 0.70, 100.0, 100.5),
		inPlayCandidate("supply", market.Sell, 0.72, 100.6, 101.0),
	}

	decisions := arbitration.ArbitrateInPlay(live, cfg, 100.55)

	for _, id := range []string{"demand", "supply"} {
		if got := decisionFor(decisions, id); got.Status != arbitration.StatusConflictHeld {
			t.Fatalf("%s: a genuine in-play near-tie must still hold, got %s", id, got.Status)
		}
	}
}

func TestArbitrateInPlay_ExecutableCandidateDoesNotConflictWithWaitingOpposite(t *testing.T) {
	cfg := arbitration.Config{ConflictMarginQuality: 0.15, InPlayATR: 1.0}
	live := []opportunity.Candidate{
		inPlayCandidate("buy_ready", market.Buy, 0.70, 100.0, 100.5),
		inPlayCandidate("sell_waiting", market.Sell, 1.0, 101.0, 101.5),
	}

	decisions := arbitration.ArbitrateInPlay(live, cfg, 100.2)
	if got := decisionFor(decisions, "buy_ready"); got.Status != arbitration.StatusUncontested {
		t.Fatalf("executable candidate must be uncontested, got %s/%s", got.Status, got.ReasonCode)
	}
	if got := decisionFor(decisions, "sell_waiting"); got.Status != arbitration.StatusSuppressed || got.ReasonCode != arbitration.ReasonNotInPlay {
		t.Fatalf("waiting opposite candidate must not block execution, got %s/%s", got.Status, got.ReasonCode)
	}
}

func TestArbitrateInPlay_DistanceIsMeasuredFromTheNearestEdgeInCandidateATR(t *testing.T) {
	cfg := arbitration.Config{ConflictMarginQuality: 0.15, InPlayATR: 1.0}
	near := inPlayCandidate("near", market.Buy, 0.7, 100.5, 101.0)
	atEdge := inPlayCandidate("edge", market.Sell, 0.7, 98.0, 99.0) // reference 100.0 is exactly 1 ATR above its high edge
	noATR := inPlayCandidate("no_atr", market.Sell, 0.9, 100.0, 100.5)
	noATR.Technical = nil

	decisions := arbitration.ArbitrateInPlay([]opportunity.Candidate{near, atEdge, noATR}, cfg, 100.0)

	if got := decisionFor(decisions, "no_atr"); got.ReasonCode != arbitration.ReasonNotInPlay {
		t.Fatalf("a candidate with no technical ATR cannot be measured and must fail closed, got %s", got.ReasonCode)
	}
	if got := decisionFor(decisions, "edge"); got.ReasonCode == arbitration.ReasonNotInPlay {
		t.Fatalf("a candidate exactly InPlayATR from price is in play, got %s", got.ReasonCode)
	}
}

func TestArbitrateInPlay_DisabledOrNoReferenceFallsBackToTheWholeLiveSet(t *testing.T) {
	live := []opportunity.Candidate{
		inPlayCandidate("a", market.Buy, 0.70, 100.0, 100.5),
		inPlayCandidate("b", market.Sell, 0.72, 120.0, 120.5),
	}
	for name, run := range map[string][]arbitration.Decision{
		"disabled":     arbitration.ArbitrateInPlay(live, arbitration.Config{ConflictMarginQuality: 0.15}, 100.2),
		"no_reference": arbitration.ArbitrateInPlay(live, arbitration.Config{ConflictMarginQuality: 0.15, InPlayATR: 1.0}, 0),
	} {
		for _, d := range run {
			if d.Status != arbitration.StatusConflictHeld {
				t.Fatalf("%s: expected the unfiltered near-tie hold, got %s for %s", name, d.Status, d.CandidateID)
			}
		}
	}
}

func withBias(c opportunity.Candidate, bias market.Direction) opportunity.Candidate {
	if c.Technical == nil {
		c.Technical = &opportunity.TechnicalContext{ATR: 1.0}
	}
	c.Technical.BiasDirection = bias
	return c
}

func TestArbitrate_NearTieIsBrokenByTheSideAlignedWithStructuralBias(t *testing.T) {
	cfg := arbitration.Config{ConflictMarginQuality: 0.15}
	counter := withBias(candidate("buy_counter", market.Buy, 1.0), market.Sell)
	aligned := withBias(candidate("sell_aligned", market.Sell, 0.92), market.Sell)

	decisions := arbitration.Arbitrate([]opportunity.Candidate{counter, aligned}, cfg)

	if got := decisionFor(decisions, "sell_aligned"); got.Status != arbitration.StatusWinner {
		t.Fatalf("the bias-aligned side of a near-tie must win, got %s/%s", got.Status, got.ReasonCode)
	}
	if got := decisionFor(decisions, "buy_counter"); got.Status != arbitration.StatusSuppressed {
		t.Fatalf("the counter-bias side of a near-tie must be suppressed, got %s", got.Status)
	}
}

func TestArbitrate_NearTieStillHoldsWhenBothOrNeitherSideIsAligned(t *testing.T) {
	cfg := arbitration.Config{ConflictMarginQuality: 0.15}
	for name, live := range map[string][]opportunity.Candidate{
		"neither": {
			withBias(candidate("b", market.Buy, 1.0), market.Direction("")),
			withBias(candidate("s", market.Sell, 0.95), market.Direction("")),
		},
		"both": {
			withBias(candidate("b1", market.Buy, 1.0), market.Buy),
			withBias(candidate("s1", market.Sell, 0.95), market.Sell),
		},
	} {
		for _, d := range arbitration.Arbitrate(live, cfg) {
			if d.Status != arbitration.StatusConflictHeld {
				t.Fatalf("%s: expected hold, got %s for %s", name, d.Status, d.CandidateID)
			}
		}
	}
}

func TestArbitrate_DecisiveQualityStillBeatsBiasAlignment(t *testing.T) {
	cfg := arbitration.Config{ConflictMarginQuality: 0.15}
	strong := withBias(candidate("buy_counter", market.Buy, 0.95), market.Sell)
	weak := withBias(candidate("sell_aligned", market.Sell, 0.60), market.Sell)

	decisions := arbitration.Arbitrate([]opportunity.Candidate{strong, weak}, cfg)

	if got := decisionFor(decisions, "buy_counter"); got.Status != arbitration.StatusWinner {
		t.Fatalf("a decisively higher quality must win regardless of bias, got %s", got.Status)
	}
}
