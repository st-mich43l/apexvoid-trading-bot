package kafka

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func TestAMDContextIsPublishedWithItsEventsAndProvenance(t *testing.T) {
	m := &opportunity.MADContext{
		Version: 3, Phase: "manip", Confidence: 0.6, Direction: "BUY", SweepSide: "low", Reclaim: true, ReasonCode: "asia_sweep_reclaim",
		Timeframe: "M5", Active: true, ObservedAt: 1000,
		Range:    &opportunity.MADRange{ID: "asia:100", Kind: "asia", Timeframe: "M5", High: 10, Low: 9, FormedFrom: 100, SealedAt: 900, BarCount: 108, ExpectedBars: 108},
		Sequence: &opportunity.MADSequence{ID: "asia:100:low:950", Status: "partial", Stages: []string{"range", "sweep", "reclaim"}, Direction: "BUY", SweepSide: "low", SweptLevel: 9, SweepAt: 950, ReclaimAt: 950},
		Relation: "aligned", RelationReasons: []string{"sweep_reclaim_in_setup_direction"}, EvidenceIDs: []string{"asia:100", "asia:100:low:950"},
	}
	raw, err := json.Marshal(madContextPayload(m))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		`"timeframe":"M5"`, `"active":true`, `"observed_at":1000`, `"range":{"id":"asia:100","kind":"asia","timeframe":"M5","high":10,"low":9,"formed_from":100,"sealed_at":900,"bar_count":108,"expected_bars":108}`,
		`"sequence":{"id":"asia:100:low:950","status":"partial","stages":["range","sweep","reclaim"],"direction":"BUY","sweep_side":"low","swept_level":9,"sweep_at":950,"reclaim_at":950}`,
		`"relation":"aligned"`, `"relation_reasons":["sweep_reclaim_in_setup_direction"]`, `"evidence_ids":["asia:100","asia:100:low:950"]`,
	} {
		if !strings.Contains(string(raw), want) {
			t.Errorf("payload %s lacks %s", raw, want)
		}
	}
}

// An event from before the AMD sequence (or a context with none) carries none of the new fields: nothing is invented, and an old payload still decodes.
func TestAMDContextWithoutTheNewFieldsStaysCompatible(t *testing.T) {
	raw, err := json.Marshal(madContextPayload(&opportunity.MADContext{Version: 2, Phase: "unclear", ReasonCode: "no_mad_signature"}))
	if err != nil {
		t.Fatal(err)
	}
	for _, absent := range []string{"timeframe", "active", "observed_at", "range", "sequence", "relation", "evidence_ids"} {
		if strings.Contains(string(raw), `"`+absent+`"`) {
			t.Errorf("a context with no AMD sequence must not publish %q: %s", absent, raw)
		}
	}
	var old MADContextPayload
	if err := json.Unmarshal([]byte(`{"version":2,"phase":"accum","confidence":0.7,"affinity":0,"reclaim":false,"reason_code":"asia_box_accum"}`), &old); err != nil {
		t.Fatal(err)
	}
	if old.Sequence != nil || old.Range != nil || old.Relation != "" || old.Active != nil {
		t.Fatalf("an old payload must decode with the new fields absent: %+v", old)
	}
	if madContextPayload(nil) != nil {
		t.Fatal("no context stays no context")
	}
}
