package shadowaudit_test

import (
	"encoding/json"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/shadowaudit"
	transport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
	"testing"
)

func record(t *testing.T, topic, eventID string, payload any) shadowaudit.Record {
	t.Helper()
	p, e := json.Marshal(payload)
	if e != nil {
		t.Fatal(e)
	}
	env := transport.Envelope{EventID: eventID, EventType: topic, EventVersion: 1, OccurredAt: 110, ProducedAt: 112, Producer: "test", CorrelationID: eventID, ConfigVersion: 3, ConfigFingerprint: "cfg", Payload: p}
	b, e := json.Marshal(env)
	if e != nil {
		t.Fatal(e)
	}
	return shadowaudit.Record{Topic: topic, Partition: 0, Offset: 1, Value: b}
}
func creation(id string) transport.OpportunityPayload {
	return transport.OpportunityPayload{ID: id, Strategy: "fvg", Symbol: "XAU", Direction: "BUY", Entry: transport.EntryZonePayload{Low: 99, High: 100}, Invalidation: transport.PriceLevelPayload{Price: 98}, Targets: []transport.TargetPayload{{Price: transport.PriceLevelPayload{Price: 102}}}, Evidence: []transport.EvidencePayload{{Code: "source_tf:M5"}}, Quality: transport.QualityPayload{Overall: .8}, AlgorithmVersion: transport.AlgorithmVersionPayload{Structure: "v2", Liquidity: "v1"}, FormedAt: 100, CreatedAt: 110, ExpiresAt: 200}
}
func terminal(id string) transport.OpportunityInvalidatedPayload {
	return transport.OpportunityInvalidatedPayload{OpportunityID: id, Symbol: "XAU", Strategy: "fvg", ReasonCode: "SETUP_EXPIRED", InvalidatedAt: 150}
}
func TestLifecycleClassificationAndLatency(t *testing.T) {
	a := shadowaudit.New([]shadowaudit.Boundary{{Topic: shadowaudit.CreatedEvent, Partition: 0, Start: 1, End: 4}}, []string{"before"}, false)
	a.Add(record(t, shadowaudit.CreatedEvent, "e1", creation("in-window")))
	a.Add(record(t, shadowaudit.TerminalEvent, "e2", terminal("in-window")))
	a.Add(record(t, shadowaudit.TerminalEvent, "e3", terminal("before")))
	a.Add(record(t, shadowaudit.TerminalEvent, "e4", terminal("unknown")))
	r := a.Report()
	if r.MatchedTerminals != 1 || r.BaselineMatched != 1 || r.UnknownPreWindow != 1 || r.OrphanTerminals != 0 {
		t.Fatalf("unexpected lifecycle report: %+v", r)
	}
	if r.OpportunityAgeSeconds.P50 != 10 || r.PublicationLagSeconds.P50 != 2 {
		t.Fatalf("unexpected latency: %+v", r)
	}
}
func TestCleanEpochDuplicatesDriftAndOrphan(t *testing.T) {
	a := shadowaudit.New(nil, nil, true)
	a.Add(record(t, shadowaudit.CreatedEvent, "e1", creation("same")))
	changed := creation("same")
	changed.Entry.Low = 97
	a.Add(record(t, shadowaudit.CreatedEvent, "e2", changed))
	a.Add(record(t, shadowaudit.TerminalEvent, "e3", terminal("orphan")))
	a.Add(record(t, shadowaudit.TerminalEvent, "e4", terminal("orphan")))
	r := a.Report()
	if r.DuplicateCreations != 1 || r.IdentityDrift != 1 || r.OrphanTerminals != 1 || r.DuplicateTerminals != 1 {
		t.Fatalf("unexpected report: %+v", r)
	}
}

func TestCrossTopicFetchOrderDoesNotCreateFalseOrphan(t *testing.T) {
	a := shadowaudit.New(nil, nil, true)
	a.Add(record(t, shadowaudit.TerminalEvent, "terminal-first", terminal("same")))
	a.Add(record(t, shadowaudit.CreatedEvent, "creation-second", creation("same")))
	r := a.Report()
	if r.MatchedTerminals != 1 || r.OrphanTerminals != 0 {
		t.Fatalf("bounded reconciliation must ignore cross-topic fetch order: %+v", r)
	}
}
