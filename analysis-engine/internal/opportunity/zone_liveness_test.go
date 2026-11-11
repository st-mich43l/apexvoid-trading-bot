package opportunity

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func zoneCandidate(t *testing.T, strategy StrategyID, setup, zoneID string, frame market.Timeframe) Candidate {
	t.Helper()
	id, err := DeterministicID(Identity{Strategy: strategy, StrategyVersion: "v1", Symbol: "XAUUSD", Direction: market.Buy, SetupKey: setup})
	if err != nil {
		t.Fatal(err)
	}
	c := Candidate{
		ID: id, Strategy: strategy, StrategyVersion: "v1", Symbol: "XAUUSD", Direction: market.Buy,
		ObservedTimeframe: market.M5, StructureTimeframe: frame, ZoneID: zoneID,
		Entry: EntryZone{Low: 4300, High: 4303}, Invalidation: market.PriceLevel{Price: 4295, Label: "stop"},
		Targets:  []Target{{Price: market.PriceLevel{Price: 4320}}},
		Evidence: []Evidence{{Code: "zone"}}, FormedAt: 100, CreatedAt: 100, ExpiresAt: 100 + 86400,
		Provenance: AnalysisProvenance{StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: "f"},
	}
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	return c
}

func liveBook(t *testing.T, candidates ...Candidate) *Book {
	t.Helper()
	b := NewBook()
	for _, c := range candidates {
		if _, err := b.Observe(c, c.CreatedAt); err != nil {
			t.Fatal(err)
		}
	}
	return b
}

func liveness(frame market.Timeframe, live []string, terminal map[string]ReasonCode) ZoneLiveness {
	z := ZoneLiveness{Frames: map[market.Timeframe]struct{}{frame: {}}, Live: map[string]struct{}{}, Terminal: terminal}
	for _, id := range live {
		z.Live[id] = struct{}{}
	}
	return z
}

func TestRetireByZonesTerminalReasons(t *testing.T) {
	cases := []struct {
		name     string
		verdict  func(zone string) ZoneLiveness
		wantCode ReasonCode
	}{
		{"zone invalidated", func(z string) ZoneLiveness {
			return liveness(market.M15, nil, map[string]ReasonCode{z: ReasonZoneInvalidated})
		}, ReasonZoneInvalidated},
		{"zone mitigated past its touch limit", func(z string) ZoneLiveness {
			return liveness(market.M15, nil, map[string]ReasonCode{z: ReasonZoneMitigated})
		}, ReasonZoneMitigated},
		{"zone closed through", func(z string) ZoneLiveness {
			return liveness(market.M15, nil, map[string]ReasonCode{z: ReasonStructureClosedThrough})
		}, ReasonStructureClosedThrough},
		{"zone expired: no longer held at all", func(z string) ZoneLiveness { return liveness(market.M15, []string{"zone:other"}, nil) }, ReasonZoneExpired},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := zoneCandidate(t, "supply", "a", "zone:A", market.M15)
			b := liveBook(t, c)
			got := b.RetireByZones("supply", tc.verdict("zone:A"), 200)
			wantKind, wantState := TransitionInvalidated, StateInvalidated
			if tc.wantCode == ReasonZoneExpired {
				wantKind, wantState = TransitionExpired, StateExpired // a zone that ran out expires its opportunity; price did not break it
			}
			if len(got) != 1 || got[0].Kind != wantKind || got[0].Record.State != wantState || got[0].Record.Terminal.Reason != tc.wantCode {
				t.Fatalf("want one %s(%s), got %+v", wantKind, tc.wantCode, got)
			}
			if !got[0].ShouldPublish() {
				t.Fatal("a zone retirement must publish the compatible terminal event")
			}
			if len(b.Live()) != 0 {
				t.Fatal("the retired opportunity must leave the live book")
			}
		})
	}
}

// A zone that is merely remote or ineligible is still live: price moving away is not structural invalidation.
func TestRetireByZonesLeavesLiveZoneAlone(t *testing.T) {
	c := zoneCandidate(t, "supply", "a", "zone:A", market.M15)
	b := liveBook(t, c)
	if got := b.RetireByZones("supply", liveness(market.M15, []string{"zone:A"}, nil), 200); len(got) != 0 {
		t.Fatalf("a live (remote/dormant) zone must not retire its opportunity: %+v", got)
	}
	if len(b.Live()) != 1 {
		t.Fatal("opportunity must stay live")
	}
}

func TestRetireByZonesEndsEveryOpportunityOfTheZoneOnly(t *testing.T) {
	a1 := zoneCandidate(t, "supply", "a1", "zone:A", market.M15)
	a2 := zoneCandidate(t, "supply", "a2", "zone:A", market.M15)
	b1 := zoneCandidate(t, "supply", "b1", "zone:B", market.M15)
	otherStrategy := zoneCandidate(t, "fvg", "a3", "zone:A", market.M15)
	otherFrame := zoneCandidate(t, "supply", "a4", "zone:A", market.H1)
	noZone := zoneCandidate(t, "supply", "nz", "", market.M15)
	book := liveBook(t, a1, a2, b1, otherStrategy, otherFrame, noZone)
	got := book.RetireByZones("supply", liveness(market.M15, []string{"zone:B"}, map[string]ReasonCode{"zone:A": ReasonZoneInvalidated}), 200)
	if len(got) != 2 {
		t.Fatalf("both opportunities of zone A must end, got %d", len(got))
	}
	for _, id := range []string{b1.ID, otherStrategy.ID, otherFrame.ID, noZone.ID} {
		if rec, _ := book.Record(id); rec.State != StateCreated {
			t.Fatalf("%s must be untouched, is %s", id, rec.State)
		}
	}
}

func TestRetireByZonesIsIdempotentAndIgnoresUnreadFrames(t *testing.T) {
	c := zoneCandidate(t, "supply", "a", "zone:A", market.M15)
	b := liveBook(t, c)
	verdict := liveness(market.M15, nil, map[string]ReasonCode{"zone:A": ReasonZoneInvalidated})
	if got := b.RetireByZones("supply", verdict, 200); len(got) != 1 {
		t.Fatalf("first verdict must retire once, got %d", len(got))
	}
	if got := b.RetireByZones("supply", verdict, 201); len(got) != 0 {
		t.Fatalf("a duplicate verdict must publish nothing, got %d", len(got))
	}
	// A verdict that did not read the opportunity's frame (data missing) never retires it, even though the zone is absent from it.
	d := zoneCandidate(t, "supply", "d", "zone:D", market.H1)
	b2 := liveBook(t, d)
	if got := b2.RetireByZones("supply", liveness(market.M15, nil, nil), 200); len(got) != 0 {
		t.Fatalf("an unread frame must never expire an opportunity: %+v", got)
	}
}

// A replay or a restart rebuilds the book from the same closed bars: a terminal zone can never reopen the executable opportunity.
func TestRetiredOpportunityStaysTerminalOnReobservation(t *testing.T) {
	c := zoneCandidate(t, "supply", "a", "zone:A", market.M15)
	b := liveBook(t, c)
	b.RetireByZones("supply", liveness(market.M15, nil, map[string]ReasonCode{"zone:A": ReasonZoneInvalidated}), 200)
	got, err := b.Observe(c, 300)
	if err != nil || got.Kind != TransitionNoop || got.ShouldPublish() {
		t.Fatalf("re-observing a retired opportunity must be a published-nothing noop: %+v %v", got, err)
	}
	if len(b.Live()) != 0 {
		t.Fatal("the opportunity must stay out of the live book")
	}
}
