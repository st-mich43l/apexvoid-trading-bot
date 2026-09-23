package opportunity_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func candidate(id string) opportunity.Candidate {
	return opportunity.Candidate{
		ID: id, Strategy: "breakout_retest", StrategyVersion: "v1", Symbol: "XAU", Direction: market.Buy,
		Entry:        opportunity.EntryZone{Low: 2000, High: 2002},
		Invalidation: market.PriceLevel{Price: 1990, Label: "protected_low"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 2020, Label: "opposing_liquidity"}}},
		Evidence:     []opportunity.Evidence{{Code: "M5_BOS_UP"}},
		Quality:      opportunity.StrategyQuality{Overall: 0.8, Components: map[string]float64{"retest": 0.9}},
		CreatedAt:    10, ExpiresAt: 30,
		Provenance: opportunity.AnalysisProvenance{StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: "fixture"},
	}
}

func TestDeterministicID_IsStableAndSeparatesSemanticSetups(t *testing.T) {
	identity := opportunity.Identity{Strategy: "breakout_retest", StrategyVersion: "v1", Symbol: "XAU", Direction: market.Buy, SetupKey: "swing-42:retest-7"}
	first, err := opportunity.DeterministicID(identity)
	if err != nil {
		t.Fatal(err)
	}
	second, err := opportunity.DeterministicID(identity)
	if err != nil {
		t.Fatal(err)
	}
	if first != second {
		t.Fatalf("same semantic identity must be stable: %q != %q", first, second)
	}
	identity.SetupKey = "swing-43:retest-7"
	different, err := opportunity.DeterministicID(identity)
	if err != nil {
		t.Fatal(err)
	}
	if first == different {
		t.Fatal("different setup keys must not deduplicate")
	}
}

func TestBook_CreatedThenActiveThenDuplicateWithoutRepublishing(t *testing.T) {
	book := opportunity.NewBook()
	c := candidate("opp-a")

	created, err := book.Observe(c, 11)
	if err != nil {
		t.Fatal(err)
	}
	if created.Kind != opportunity.TransitionCreated || !created.ShouldPublish() || created.Record.State != opportunity.StateCreated {
		t.Fatalf("unexpected created transition: %+v", created)
	}
	active, err := book.Observe(c, 12)
	if err != nil {
		t.Fatal(err)
	}
	if active.Kind != opportunity.TransitionActivated || active.ShouldPublish() || active.Record.State != opportunity.StateActive {
		t.Fatalf("unexpected active transition: %+v", active)
	}
	duplicate, err := book.Observe(c, 13)
	if err != nil {
		t.Fatal(err)
	}
	if duplicate.Kind != opportunity.TransitionDuplicate || duplicate.ShouldPublish() {
		t.Fatalf("re-evaluation must be suppressed, got %+v", duplicate)
	}
	if live := book.Live(); len(live) != 1 || live[0].ID != c.ID {
		t.Fatalf("expected one live opportunity, got %+v", live)
	}
}

func TestBook_InvalidatesExactlyOnceWithMachineReadableReason(t *testing.T) {
	book := opportunity.NewBook()
	c := candidate("opp-invalidated")
	if _, err := book.Observe(c, 11); err != nil {
		t.Fatal(err)
	}

	invalidated, err := book.Invalidate(c.ID, opportunity.ReasonStructureInvalidated, 15)
	if err != nil {
		t.Fatal(err)
	}
	if invalidated.Kind != opportunity.TransitionInvalidated || !invalidated.ShouldPublish() || invalidated.Record.State != opportunity.StateInvalidated || invalidated.Record.Terminal == nil || invalidated.Record.Terminal.Reason != opportunity.ReasonStructureInvalidated {
		t.Fatalf("unexpected invalidation: %+v", invalidated)
	}
	if live := book.Live(); len(live) != 0 {
		t.Fatalf("invalidated opportunity must not remain live: %+v", live)
	}
	repeated, err := book.Invalidate(c.ID, opportunity.ReasonStructureInvalidated, 16)
	if err != nil {
		t.Fatal(err)
	}
	if repeated.Kind != opportunity.TransitionNoop || repeated.ShouldPublish() {
		t.Fatalf("invalidation must publish once, got %+v", repeated)
	}
	if _, err := book.Invalidate(c.ID, "human readable reason", 17); err == nil {
		t.Fatal("free-text reason must be rejected")
	}
	if _, err := book.Invalidate(c.ID, "_INVALID", 17); err == nil {
		t.Fatal("reason code must begin with an uppercase letter")
	}
}

func TestBook_ExpiresOnlyFromStrategyTechnicalDeadline(t *testing.T) {
	book := opportunity.NewBook()
	c := candidate("opp-expired")
	if _, err := book.Observe(c, 11); err != nil {
		t.Fatal(err)
	}
	if expired := book.Expire(29); len(expired) != 0 {
		t.Fatalf("opportunity expired before its technical deadline: %+v", expired)
	}
	expired := book.Expire(30)
	if len(expired) != 1 || expired[0].Kind != opportunity.TransitionExpired || !expired[0].ShouldPublish() || expired[0].Record.Terminal == nil || expired[0].Record.Terminal.Reason != opportunity.ReasonSetupExpired {
		t.Fatalf("unexpected expiry: %+v", expired)
	}
	if again := book.Expire(31); len(again) != 0 {
		t.Fatalf("expiry must be emitted once, got %+v", again)
	}
}

func TestBook_RejectsIdentityCollisionAndProtectsStoredCopies(t *testing.T) {
	book := opportunity.NewBook()
	c := candidate("opp-copy")
	if _, err := book.Observe(c, 11); err != nil {
		t.Fatal(err)
	}
	collision := c
	collision.Direction = market.Sell
	if _, err := book.Observe(collision, 12); err == nil {
		t.Fatal("same ID with a different semantic setup must fail")
	}

	live := book.Live()
	live[0].Evidence[0].Code = "MUTATED"
	live[0].Quality.Components["retest"] = 0
	record, ok := book.Record(c.ID)
	if !ok {
		t.Fatal("missing stored record")
	}
	if record.Candidate.Evidence[0].Code != "M5_BOS_UP" || record.Candidate.Quality.Components["retest"] != 0.9 {
		t.Fatalf("book leaked mutable candidate state: %+v", record.Candidate)
	}
}

func TestBook_ObservationDoesNotImplicitlyInvalidateAbsentOpportunity(t *testing.T) {
	book := opportunity.NewBook()
	first := candidate("opp-first")
	second := candidate("opp-second")
	if _, err := book.Observe(first, 11); err != nil {
		t.Fatal(err)
	}
	if _, err := book.Observe(second, 12); err != nil {
		t.Fatal(err)
	}
	if live := book.Live(); len(live) != 2 {
		t.Fatalf("strategy absence is not technical invalidation, got %+v", live)
	}
}

func TestBook_RejectsIncompleteCandidateContract(t *testing.T) {
	book := opportunity.NewBook()
	c := candidate("opp-incomplete")
	c.Provenance.ConfigFingerprint = ""
	if _, err := book.Observe(c, 11); err == nil {
		t.Fatal("candidate without configuration provenance must fail")
	}
	c = candidate("opp-incomplete")
	c.Evidence = nil
	if _, err := book.Observe(c, 11); err == nil {
		t.Fatal("candidate without machine-readable evidence must fail")
	}
}

func TestBook_RejectsOutOfOrderLifecycleTransitions(t *testing.T) {
	book := opportunity.NewBook()
	c := candidate("opp-causal")
	if _, err := book.Observe(c, 12); err != nil {
		t.Fatal(err)
	}
	if _, err := book.Observe(c, 11); err == nil {
		t.Fatal("out-of-order observation must fail")
	}
	if _, err := book.Invalidate(c.ID, opportunity.ReasonRetestFailed, 11); err == nil {
		t.Fatal("out-of-order invalidation must fail")
	}
}
