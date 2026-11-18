package opportunity

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// zdCandidate is a SELL supply setup: structure 4300-4304.5, entry band from the recommended price 4301 up to the zone's far edge, invalidation above it.
func zdCandidate(t *testing.T) Candidate {
	t.Helper()
	c := zoneCandidate(t, "supply", "zone", "zone:Z", market.M15)
	c.Direction = market.Sell
	c.Entry = EntryZone{Low: 4301, High: 4304.5}
	c.Invalidation = market.PriceLevel{Price: 4308, Label: "supply_zone_invalidated"}
	c.Targets = []Target{{Price: market.PriceLevel{Price: 4290, Label: "nearest_opposing_structure"}}}
	c.Structure = &StructureZone{Timeframe: market.M15, Low: 4300, High: 4304.5, FormedAt: 100, Swing: &ZoneSwing{
		ZoneID: "zone", Class: "MAJOR", Strength: "strong", AnchorPrice: 4304.5, ProximalPrice: 4300, DistalPrice: 4304.5, SwingConfirmedAt: 100, BaseFormedAt: 100, DepartureConfirmedAt: 200,
		BaseCandles: 5, ProminenceATR: 7, DepartureATR: 6, Efficiency: 0.7, External: true, Shift: "CHoCH",
	}}
	c.EntryPlan = &EntryPlan{Model: "zone_candidates_v1", Kind: "stop_cap_fit", Price: 4301, DepthFraction: 0.2, StopPips: 70, EffectiveRisk: 7, RoomR: 1.5, Target: 4290.5, TargetLabel: "nearest_opposing_structure"}
	if err := c.Validate(); err != nil {
		t.Fatalf("fixture invalid: %v", err)
	}
	return c
}

func TestZoneDerivationIsCoherentWithTheGeometryItDescribes(t *testing.T) {
	cases := map[string]func(*Candidate){
		"swing without a class":                       func(c *Candidate) { c.Structure.Swing.Class = "" },
		"swing confirmed at no time":                  func(c *Candidate) { c.Structure.Swing.SwingConfirmedAt = 0 },
		"proximal edge outside the structure":         func(c *Candidate) { c.Structure.Swing.ProximalPrice = 4299 },
		"distal edge outside the structure":           func(c *Candidate) { c.Structure.Swing.DistalPrice = 4310 },
		"swing measure not finite":                    func(c *Candidate) { c.Structure.Swing.ProminenceATR = nan() },
		"entry plan without a model":                  func(c *Candidate) { c.EntryPlan.Model = "" },
		"entry plan price outside the published band": func(c *Candidate) { c.EntryPlan.Price = 4310 },
		"entry plan with no risk":                     func(c *Candidate) { c.EntryPlan.EffectiveRisk = 0 },
		"entry plan candidate without a kind":         func(c *Candidate) { c.EntryPlan.Candidates = []EntryPlanCandidate{{Price: 1}} },
	}
	for name, mutate := range cases {
		c := zdCandidate(t)
		swing := *c.Structure.Swing
		structure := *c.Structure
		structure.Swing = &swing
		c.Structure = &structure
		plan := *c.EntryPlan
		c.EntryPlan = &plan
		mutate(&c)
		if err := c.Validate(); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

// Normalising to the broker tick keeps the recommended entry a price of the (possibly widened) band, and cloning never shares the blocks.
func TestEntryPlanStaysInTheBandAfterTickNormalisationAndIsCloned(t *testing.T) {
	c := zdCandidate(t)
	c.EntryPlan.Price = 4301.004
	got, err := NormalizeGeometry(c, market.Geometry{Symbol: "XAUUSD", PipSize: 0.1, PriceDigits: 2})
	if err != nil {
		t.Fatal(err)
	}
	if got.EntryPlan.Price < got.Entry.Low || got.EntryPlan.Price > got.Entry.High {
		t.Fatalf("plan price %v left the band %+v", got.EntryPlan.Price, got.Entry)
	}
	clone := cloneCandidate(got)
	clone.EntryPlan.Price, clone.Structure.Swing.Class = 1, "MINOR"
	if got.EntryPlan.Price == 1 {
		t.Fatal("a clone must not share the entry plan")
	}
	if got.Structure.Swing.Class == "MINOR" {
		t.Fatal("a clone must not share the swing")
	}
}
