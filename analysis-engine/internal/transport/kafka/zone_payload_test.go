package kafka

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func TestSwingAndEntryPlanArePublishedForASupplyOrDemandZone(t *testing.T) {
	raw, err := json.Marshal(OpportunityPayload{
		ID: "opp", Strategy: "supply", Symbol: "XAU", Direction: "SELL",
		Structure: &StructurePayload{Timeframe: "M15", Low: 4300, High: 4304.5, FormedAt: 1800000000, Swing: &ZoneSwingPayload{
			ZoneID: "z", Class: "MAJOR", Strength: "strong", AnchorPrice: 4304.5, ProximalPrice: 4300, DistalPrice: 4304.5, SwingConfirmedAt: 1, BaseFormedAt: 2, DepartureConfirmedAt: 3, BaseCandles: 5,
			ProminenceATR: 7.5, DepartureATR: 6.2, Efficiency: 0.7, External: true, Shift: "CHoCH",
		}},
		EntryPlan: entryPlanPayload(&opportunity.EntryPlan{
			Model: "zone_candidates_v1", Kind: "stop_cap_fit", Price: 4301, DepthFraction: 0.22, StopPips: 70, EffectiveRisk: 7, RoomR: 1.4, Target: 4291.2, TargetLabel: "nearest_opposing_structure",
			Candidates: []opportunity.EntryPlanCandidate{{Kind: "proximal", Price: 4300, Feasible: false, Rejected: "stop_beyond_cap"}, {Kind: "stop_cap_fit", Price: 4301, DepthFraction: 0.22, Feasible: true}},
		}),
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		`"swing":{"zone_id":"z","major_swing_class":"MAJOR","zone_strength":"strong","swing_anchor_price":4304.5,"proximal_price":4300,"distal_price":4304.5`,
		`"shift":"CHoCH"`, `"entry_plan":{"model":"zone_candidates_v1","kind":"stop_cap_fit","price":4301`,
		`{"kind":"proximal","price":4300,"depth_fraction":0,"feasible":false,"rejected":"stop_beyond_cap"}`,
	} {
		if !strings.Contains(string(raw), want) {
			t.Errorf("payload %s lacks %s", raw, want)
		}
	}
}

// Any other opportunity, and a zone of the legacy definition, carries neither block: nothing is invented and an older event decodes unchanged.
func TestZoneBlocksAreOmittedWhenThereIsNothingToSay(t *testing.T) {
	raw, err := json.Marshal(OpportunityPayload{ID: "opp", Strategy: "fvg", Structure: &StructurePayload{Timeframe: "M15", Low: 1, High: 2, FormedAt: 3}})
	if err != nil {
		t.Fatal(err)
	}
	for _, absent := range []string{"swing", "entry_plan"} {
		if strings.Contains(string(raw), `"`+absent+`"`) {
			t.Errorf("must not publish %q: %s", absent, raw)
		}
	}
	var old OpportunityPayload
	if err := json.Unmarshal([]byte(`{"id":"opp","strategy":"supply","structure":{"timeframe":"M15","low":1,"high":2,"formed_at":3}}`), &old); err != nil {
		t.Fatal(err)
	}
	if old.Structure == nil || old.Structure.Swing != nil || old.EntryPlan != nil {
		t.Fatalf("an old event decodes with the new blocks absent: %+v", old)
	}
	if entryPlanPayload(nil) != nil {
		t.Fatal("no plan stays no plan")
	}
}
