package engine_test

import (
	"context"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// fakeKafkaClient is a real, in-memory implementation of
// engine.OpportunityKafkaClient — no fake framework, no reflection, just
// the same interface *kafka.Producer satisfies, recording every call so
// tests can assert on it without a real broker.
type fakeKafkaClient struct {
	mu sync.Mutex

	failNextOpportunity int // PublishOpportunity fails this many more times before succeeding
	failNextInvalidated int
	opportunities       []opportunity.Candidate
	invalidations       []kafka.OpportunityInvalidatedPayload
	calls               []string // "created" / "invalidated", in call order
	eventIDs            []string
}

func (f *fakeKafkaClient) PublishOpportunity(_ context.Context, eventID, _, _ string, candidate opportunity.Candidate, _ kafka.AlgorithmVersion, _ time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.eventIDs = append(f.eventIDs, eventID)
	if f.failNextOpportunity > 0 {
		f.failNextOpportunity--
		return context.DeadlineExceeded
	}
	f.opportunities = append(f.opportunities, candidate)
	f.calls = append(f.calls, "created:"+candidate.ID)
	return nil
}

func (f *fakeKafkaClient) PublishOpportunityInvalidated(_ context.Context, eventID, _, _ string, _ market.Symbol, payload kafka.OpportunityInvalidatedPayload, _ time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.eventIDs = append(f.eventIDs, eventID)
	if f.failNextInvalidated > 0 {
		f.failNextInvalidated--
		return context.DeadlineExceeded
	}
	f.invalidations = append(f.invalidations, payload)
	f.calls = append(f.calls, "invalidated:"+payload.OpportunityID)
	return nil
}

func (f *fakeKafkaClient) snapshot() ([]opportunity.Candidate, []kafka.OpportunityInvalidatedPayload, []string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]opportunity.Candidate(nil), f.opportunities...),
		append([]kafka.OpportunityInvalidatedPayload(nil), f.invalidations...),
		append([]string(nil), f.calls...)
}

func (f *fakeKafkaClient) IDs() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.eventIDs...)
}

func fakeCandidate(id string) opportunity.Candidate {
	return opportunity.Candidate{
		ID: id, Strategy: "supply", StrategyVersion: "v2", Symbol: "XAU", Direction: market.Sell,
		Entry:        opportunity.EntryZone{Low: 2000, High: 2002},
		Invalidation: market.PriceLevel{Price: 2005, Label: "zone_invalidated"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 1990, Label: "liquidity"}}},
		Evidence:     []opportunity.Evidence{{Code: "test_evidence"}},
		Quality:      opportunity.StrategyQuality{Overall: 0.8},
		CreatedAt:    10, ExpiresAt: 100,
		Provenance: opportunity.AnalysisProvenance{StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: "fixture"},
	}
}

func waitFor(t *testing.T, timeout time.Duration, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if condition() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("condition not met within %s", timeout)
}

func TestOpportunityPublisher_PublishesACreatedTransition(t *testing.T) {
	client := &fakeKafkaClient{}
	pub := engine.NewOpportunityPublisher(client, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go pub.Run(ctx)

	candidate := fakeCandidate("opp-created")
	transition := opportunity.Transition{Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: candidate, State: opportunity.StateCreated}}
	pub.Enqueue("XAU", kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"}, transition)

	waitFor(t, 2*time.Second, func() bool {
		opps, _, _ := client.snapshot()
		return len(opps) == 1
	})
	opps, _, _ := client.snapshot()
	if opps[0].ID != "opp-created" {
		t.Errorf("expected the published candidate to be opp-created, got %q", opps[0].ID)
	}
}

func TestOpportunityPublisher_PublishesInvalidatedAndExpiredToTheSameInvalidatedCall(t *testing.T) {
	client := &fakeKafkaClient{}
	pub := engine.NewOpportunityPublisher(client, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go pub.Run(ctx)

	invalidated := opportunity.Transition{
		Kind: opportunity.TransitionInvalidated,
		Record: opportunity.Record{
			Candidate: fakeCandidate("opp-invalidated"), State: opportunity.StateInvalidated,
			Terminal: &opportunity.Terminal{Reason: opportunity.ReasonZoneInvalidated, At: 50},
		},
	}
	expired := opportunity.Transition{
		Kind: opportunity.TransitionExpired,
		Record: opportunity.Record{
			Candidate: fakeCandidate("opp-expired"), State: opportunity.StateExpired,
			Terminal: &opportunity.Terminal{Reason: opportunity.ReasonSetupExpired, At: 60},
		},
	}
	// A terminal is externally meaningful only after the matching creation.
	// Queue the creations first; FIFO guarantees each precedes its terminal.
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: invalidated.Record.Candidate}})
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, invalidated)
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: expired.Record.Candidate}})
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, expired)

	waitFor(t, 2*time.Second, func() bool {
		_, inv, _ := client.snapshot()
		return len(inv) == 2
	})
	_, inv, _ := client.snapshot()
	if inv[0].OpportunityID != "opp-invalidated" || inv[0].ReasonCode != string(opportunity.ReasonZoneInvalidated) {
		t.Errorf("expected the first invalidated payload to carry the strategy-supplied reason, got %+v", inv[0])
	}
	if inv[1].OpportunityID != "opp-expired" || inv[1].ReasonCode != string(opportunity.ReasonSetupExpired) {
		t.Errorf("expected the second (Expired) payload to carry SETUP_EXPIRED on the same invalidated call, got %+v", inv[1])
	}
}

func TestOpportunityPublisher_NonPublishableTransitionsAreNeverEnqueued(t *testing.T) {
	client := &fakeKafkaClient{}
	pub := engine.NewOpportunityPublisher(client, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go pub.Run(ctx)

	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionActivated, Record: opportunity.Record{Candidate: fakeCandidate("opp-active")}})
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionDuplicate, Record: opportunity.Record{Candidate: fakeCandidate("opp-dup")}})
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionNoop, Record: opportunity.Record{Candidate: fakeCandidate("opp-noop")}})

	// Give the (idle) publisher a moment to prove it stays idle, then
	// confirm nothing was ever published.
	time.Sleep(50 * time.Millisecond)
	opps, inv, _ := client.snapshot()
	if len(opps) != 0 || len(inv) != 0 {
		t.Fatalf("expected Activated/Duplicate/Noop to never reach the client, got opportunities=%v invalidations=%v", opps, inv)
	}
}

// TestOpportunityPublisher_RetriesAFailedPublishRatherThanDroppingIt is
// the direct proof of this type's own "must never silently discard an
// opportunity" contract (cmd/analysis-engine/main.go's own comment on
// the Producer it constructs).
func TestOpportunityPublisher_RetriesAFailedPublishRatherThanDroppingIt(t *testing.T) {
	client := &fakeKafkaClient{failNextOpportunity: 2} // fails twice, then succeeds
	pub := engine.NewOpportunityPublisher(client, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go pub.Run(ctx)

	candidate := fakeCandidate("opp-retried")
	transition := opportunity.Transition{Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: candidate}}
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, transition)

	// publishRetryBackoff is 2s; two failures means >=4s before success —
	// generous but bounded, and this is the one test in this file that
	// deliberately exercises real wall-clock retry timing rather than
	// mocking it out, since the retry loop's own timing IS the behavior
	// under test.
	waitFor(t, 6*time.Second, func() bool {
		opps, _, _ := client.snapshot()
		return len(opps) == 1
	})
	opps, _, _ := client.snapshot()
	if opps[0].ID != "opp-retried" {
		t.Errorf("expected the retried candidate to eventually be delivered, got %+v", opps)
	}
}

// TestOpportunityPublisher_PreservesOrderAcrossMultipleEnqueues proves the
// queue never reorders (front-of-queue-only removal) even when several
// jobs are enqueued before Run has drained any of them.
func TestOpportunityPublisher_PreservesOrderAcrossMultipleEnqueues(t *testing.T) {
	client := &fakeKafkaClient{}
	pub := engine.NewOpportunityPublisher(client, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	for _, id := range []string{"opp-1", "opp-2", "opp-3"} {
		pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{
			Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: fakeCandidate(id)},
		})
	}
	go pub.Run(ctx)

	waitFor(t, 2*time.Second, func() bool {
		opps, _, _ := client.snapshot()
		return len(opps) == 3
	})
	_, _, calls := client.snapshot()
	want := []string{"created:opp-1", "created:opp-2", "created:opp-3"}
	if len(calls) != len(want) {
		t.Fatalf("expected %d calls, got %v", len(want), calls)
	}
	for i, w := range want {
		if calls[i] != w {
			t.Errorf("call %d: expected %q, got %q (order not preserved)", i, w, calls[i])
		}
	}
}

// TestOpportunityPublisher_TypedNilProducerTrap is a direct regression
// guard for the exact bug caught (and fixed) while wiring
// cmd/analysis-engine/main.go: a nil *kafka.Producer assigned directly
// into an OpportunityKafkaClient-typed variable produces a NON-nil
// interface (Go's classic typed-nil trap), so NewOpportunityPublisher's
// own `client == nil` check does not catch it that way. This test proves
// the correct call pattern (leaving the interface variable itself unset)
// is what actually produces a nil, no-op publisher — documentation as a
// runnable test, not just a comment.
func TestOpportunityPublisher_TypedNilProducerTrap(t *testing.T) {
	var nilProducer *fakeKafkaClient // the zero value: a nil concrete pointer

	// The WRONG way: assigning a nil concrete pointer straight into the
	// interface parameter produces a non-nil interface.
	wrong := engine.NewOpportunityPublisher(nilProducer, nil)
	if wrong == nil {
		t.Fatal("this assertion documents Go's typed-nil trap: a nil *T assigned into an interface parameter is NOT a nil interface, so NewOpportunityPublisher's own nil check cannot catch it this way — if this ever starts failing, the language/compiler behavior this test documents has changed")
	}

	// The RIGHT way (what cmd/analysis-engine/main.go actually does):
	// leave the interface-typed variable itself unset when the concrete
	// pointer is nil.
	var client engine.OpportunityKafkaClient
	if nilProducer != nil {
		client = nilProducer
	}
	right := engine.NewOpportunityPublisher(client, nil)
	if right != nil {
		t.Fatal("expected the correctly-guarded nil producer to produce a nil, no-op publisher")
	}
}

func TestOpportunityPublisher_NilClientProducesANilPublisherThatNeverPanics(t *testing.T) {
	pub := engine.NewOpportunityPublisher(nil, nil)
	if pub != nil {
		t.Fatal("expected NewOpportunityPublisher(nil, ...) to return nil")
	}
	// Must not panic on a nil receiver.
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionCreated})
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	pub.Run(ctx) // must return immediately, not block until ctx expires
}

func TestOpportunityPublisher_SuppressesTerminalWithoutPublishedCreation(t *testing.T) {
	client := &fakeKafkaClient{}
	pub := engine.NewOpportunityPublisher(client, nil)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go pub.Run(ctx)

	candidate := fakeCandidate("opp-orphan")
	pub.Observe("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{
		Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: candidate},
	}, false)
	pub.Observe("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{
		Kind:   opportunity.TransitionExpired,
		Record: opportunity.Record{Candidate: candidate, Terminal: &opportunity.Terminal{Reason: opportunity.ReasonSetupExpired, At: 100}},
	}, true)

	time.Sleep(50 * time.Millisecond)
	opps, terminals, _ := client.snapshot()
	if len(opps) != 0 || len(terminals) != 0 {
		t.Fatalf("unpublished creation must suppress its terminal, got creations=%d terminals=%d", len(opps), len(terminals))
	}
}

func TestOpportunityPublisher_DurableOutboxRecoversPendingCreation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "publication-ledger.json")
	client := &fakeKafkaClient{}
	first, err := engine.NewDurableOpportunityPublisher(client, nil, path)
	if err != nil {
		t.Fatal(err)
	}
	candidate := fakeCandidate("opp-restart")
	first.Enqueue("XAU", kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"}, opportunity.Transition{
		Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: candidate},
	})

	restarted, err := engine.NewDurableOpportunityPublisher(client, nil, path)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go restarted.Run(ctx)
	waitFor(t, 2*time.Second, func() bool {
		opps, _, _ := client.snapshot()
		return len(opps) == 1
	})

	restarted.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{
		Kind:   opportunity.TransitionExpired,
		Record: opportunity.Record{Candidate: candidate, Terminal: &opportunity.Terminal{Reason: opportunity.ReasonSetupExpired, At: 100}},
	})
	waitFor(t, 2*time.Second, func() bool {
		_, terminals, _ := client.snapshot()
		return len(terminals) == 1
	})
}

func TestOpportunityPublisher_RetryKeepsStableEventIdentityAndOrdering(t *testing.T) {
	client := &fakeKafkaClient{failNextOpportunity: 1}
	pub := engine.NewOpportunityPublisher(client, nil)
	candidate := fakeCandidate("opp-outage")
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{
		Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: candidate},
	})
	pub.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{
		Kind:   opportunity.TransitionInvalidated,
		Record: opportunity.Record{Candidate: candidate, Terminal: &opportunity.Terminal{Reason: opportunity.ReasonZoneInvalidated, At: 50}},
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go pub.Run(ctx)
	waitFor(t, 4*time.Second, func() bool {
		_, terminals, _ := client.snapshot()
		return len(terminals) == 1
	})
	_, _, calls := client.snapshot()
	if len(calls) != 2 || calls[0] != "created:opp-outage" || calls[1] != "invalidated:opp-outage" {
		t.Fatalf("creation must precede terminal after outage, got %v", calls)
	}
	ids := client.IDs()
	if len(ids) < 3 || ids[0] != ids[1] {
		t.Fatalf("retry must reuse one event ID, got %v", ids)
	}
}

func TestOpportunityPublisher_RestartAfterCreationAckRecoversPendingTerminal(t *testing.T) {
	path := filepath.Join(t.TempDir(), "publication-ledger.json")
	client := &fakeKafkaClient{}
	candidate := fakeCandidate("opp-acked-before-restart")
	first, err := engine.NewDurableOpportunityPublisher(client, nil, path)
	if err != nil {
		t.Fatal(err)
	}
	first.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: candidate}})
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { first.Run(ctx); close(done) }()
	waitFor(t, 2*time.Second, func() bool { opps, _, _ := client.snapshot(); return len(opps) == 1 })
	cancel()
	<-done
	first.Enqueue("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionExpired, Record: opportunity.Record{Candidate: candidate, Terminal: &opportunity.Terminal{Reason: opportunity.ReasonSetupExpired, At: 100}}})

	restarted, err := engine.NewDurableOpportunityPublisher(client, nil, path)
	if err != nil {
		t.Fatal(err)
	}
	restartCtx, restartCancel := context.WithCancel(context.Background())
	defer restartCancel()
	go restarted.Run(restartCtx)
	waitFor(t, 2*time.Second, func() bool { _, terminals, _ := client.snapshot(); return len(terminals) == 1 })
	opps, terminals, calls := client.snapshot()
	if len(opps) != 1 || len(terminals) != 1 || len(calls) != 2 || calls[0] != "created:opp-acked-before-restart" || calls[1] != "invalidated:opp-acked-before-restart" {
		t.Fatalf("restart must recover only the pending terminal after creation ack: calls=%v", calls)
	}
}

func TestOpportunityPublisher_DefersRecoveredTerminalUntilLiveResume(t *testing.T) {
	client := &fakeKafkaClient{}
	pub := engine.NewOpportunityPublisher(client, nil)
	candidate := fakeCandidate("opp-recovery-terminal")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go pub.Run(ctx)
	pub.Observe("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionCreated, Record: opportunity.Record{Candidate: candidate}}, true)
	waitFor(t, 2*time.Second, func() bool { opps, _, _ := client.snapshot(); return len(opps) == 1 })
	pub.Observe("XAU", kafka.AlgorithmVersion{}, opportunity.Transition{Kind: opportunity.TransitionExpired, Record: opportunity.Record{Candidate: candidate, Terminal: &opportunity.Terminal{Reason: opportunity.ReasonSetupExpired, At: 100}}}, false)
	time.Sleep(50 * time.Millisecond)
	_, terminals, _ := client.snapshot()
	if len(terminals) != 0 {
		t.Fatal("bootstrap/recovery processing must not publish terminal immediately")
	}
	pub.ResumeLive("XAU")
	waitFor(t, 2*time.Second, func() bool { _, got, _ := client.snapshot(); return len(got) == 1 })
}
