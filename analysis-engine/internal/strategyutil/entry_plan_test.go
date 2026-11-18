package strategyutil

import (
	"math"
	"testing"
)

// XAU-like budget: pip 0.1, stop floor 50 pips (5.0), cap 70 pips (7.0), a cut band at least 30 pips (3.0) wide.
func xauBudget() StopBudget {
	return StopBudget{MaxPips: 70, MinPips: 50, PipSize: 0.1, DeepEntryMinimumWidthPips: 30}
}

// below returns the obstacles below reference (a SELL's opposing structure), nearest first.
func below(levels ...float64) func(float64) []float64 {
	return func(reference float64) []float64 {
		var out []float64
		for _, l := range levels {
			if l < reference {
				out = append(out, l)
			}
		}
		for i := 0; i < len(out); i++ { // nearest (highest) first
			for j := i + 1; j < len(out); j++ {
				if out[j] > out[i] {
					out[i], out[j] = out[j], out[i]
				}
			}
		}
		return out
	}
}

func sellSpec(low, high, inv float64, obstacles ...float64) EntrySpec {
	return EntrySpec{
		Sell: true, StructLow: low, StructHigh: high, Invalidation: inv, MaxBandWidth: 5.0, MinBandWidth: 3.0, Budget: xauBudget(),
		MinimumTargetR: 1.0, FallbackTargetR: 2.0, Tick: 0.01, Obstacles: below(obstacles...),
	}
}

// mirror turns a SELL spec into the BUY spec of the mirrored market (price -> 8600 - price).
func mirror(s EntrySpec, obstacles []float64) EntrySpec {
	m := s
	m.Sell = false
	m.StructLow, m.StructHigh = 8600-s.StructHigh, 8600-s.StructLow
	m.Invalidation = 8600 - s.Invalidation
	above := func(reference float64) []float64 {
		var out []float64
		for _, l := range obstacles {
			if 8600-l > reference {
				out = append(out, 8600-l)
			}
		}
		for i := 0; i < len(out); i++ { // nearest (lowest) first
			for j := i + 1; j < len(out); j++ {
				if out[j] < out[i] {
					out[i], out[j] = out[j], out[i]
				}
			}
		}
		return out
	}
	m.Obstacles = above
	return m
}

func kinds(p EntryPlan) []string {
	var out []string
	for _, c := range p.Candidates {
		out = append(out, c.Kind)
	}
	return out
}

func TestTheProximalEdgeIsChosenWhenItIsFeasible(t *testing.T) {
	plan := PlanEntry(sellSpec(4300, 4304.5, 4305, 4290))
	c, ok := plan.Chosen()
	if !ok || c.Kind != EntryProximal || c.Price != 4300 {
		t.Fatalf("chosen %+v of %v", c, kinds(plan))
	}
	if c.BandLow != 4300 || c.BandHigh != 4304.5 {
		t.Fatalf("the band is the part of the zone from the entry: %+v", c)
	}
	if math.Abs(c.EffectiveRisk-5.0) > 1e-9 || c.StopPips < 49.9 || c.RoomR < 1.99 {
		t.Fatalf("a 5.0 stop at the 50-pip floor with 10.0 of room: %+v", c)
	}
}

// A stop beyond the cap from the proximal edge moves the ENTRY into the zone (never the stop): the price where the stop is exactly the cap.
func TestAStopBeyondTheCapMovesTheEntryNotTheStop(t *testing.T) {
	spec := sellSpec(4300, 4304.5, 4308, 4290)
	plan := PlanEntry(spec)
	c, ok := plan.Chosen()
	if !ok || c.Kind != EntryStopCapFit {
		t.Fatalf("chosen %+v of %v", c, kinds(plan))
	}
	if math.Abs(c.Price-4301.0) > 1e-9 || math.Abs(c.StopPips-70) > 1e-6 {
		t.Fatalf("the deepest-needed price risks exactly the 70-pip cap: %+v", c)
	}
	if c.BandHigh-c.BandLow < 3.0-1e-9 {
		t.Fatalf("the band stays at least the minimum width: %+v", c)
	}
	if spec.Invalidation != 4308 {
		t.Fatal("the planner must never move the invalidation")
	}
}

// A zone too thin to leave a minimum-width band deeper in refuses instead of stretching the band or moving the stop.
func TestNoFeasibleEntryRefusesRatherThanStretching(t *testing.T) {
	plan := PlanEntry(sellSpec(4300, 4303.5, 4308, 4290))
	if _, ok := plan.Chosen(); ok {
		t.Fatalf("no candidate can keep a 30-pip band within the cap: %+v", plan.Candidates)
	}
	if plan.Refusal != EntryRejectedBandTooThin {
		t.Fatalf("refusal %q", plan.Refusal)
	}
	for _, c := range plan.Candidates {
		if c.Feasible {
			t.Fatalf("a feasible candidate in a refused plan: %+v", c)
		}
	}
}

// Room: the nearest opposing structure is too close to the proximal edge; a deeper price in the zone restores it, and the room-fit price is exactly where it is met.
func TestADeeperEntryIsChosenWhenOnlyItHasRoom(t *testing.T) {
	plan := PlanEntry(sellSpec(4300, 4306, 4307, 4297.5))
	c, ok := plan.Chosen()
	if !ok || c.Kind == EntryProximal {
		t.Fatalf("the proximal edge has 2.5 of room against a 5.0 effective risk; chosen %+v of %v", c, kinds(plan))
	}
	if c.RoomR < 1.0-1e-9 {
		t.Fatalf("the chosen entry must have the minimum room: %+v", c)
	}
	first := plan.Candidates[0]
	if first.Kind != EntryProximal || first.Feasible || first.Rejected != EntryRejectedNoRoom {
		t.Fatalf("the proximal candidate fails on room: %+v", first)
	}
}

// Deeper always means a smaller stop and a larger reward:risk. That is never a reason to pick it: the shallowest feasible price is chosen even though a deeper
// candidate has a better ratio.
func TestSmallerStopIsNotAReasonToGoDeeper(t *testing.T) {
	plan := PlanEntry(sellSpec(4300, 4306, 4307, 4290))
	c, _ := plan.Chosen()
	if c.Kind != EntryProximal {
		t.Fatalf("chosen %+v", c)
	}
	deeper := 0
	for _, o := range plan.Candidates[1:] {
		if o.Feasible && o.RoomR > c.RoomR {
			deeper++
		}
	}
	if deeper == 0 {
		t.Fatal("the fixture must offer a better ratio deeper for this test to mean anything")
	}
}

func TestEffectiveRiskUsesTheInstrumentFloor(t *testing.T) {
	plan := PlanEntry(sellSpec(4300, 4304.5, 4302.5, 4290)) // a 2.5 structural stop, tighter than the 5.0 floor
	c, ok := plan.Chosen()
	if !ok || c.Kind != EntryProximal || math.Abs(c.EffectiveRisk-5.0) > 1e-9 {
		t.Fatalf("the trade is exposed to the floor, not the structural distance: %+v", c)
	}
}

// A BUY is the exact price mirror of a SELL: the same kinds, the same feasibility, the mirrored prices.
func TestDemandIsThePriceMirrorOfSupply(t *testing.T) {
	cases := []struct {
		name      string
		spec      EntrySpec
		obstacles []float64
	}{
		{"proximal", sellSpec(4300, 4304.5, 4305, 4290), []float64{4290}},
		{"stop cap", sellSpec(4300, 4304.5, 4308, 4290), []float64{4290}},
		{"room", sellSpec(4300, 4306, 4307, 4297.5), []float64{4297.5}},
		{"refused", sellSpec(4300, 4303.5, 4308, 4290), []float64{4290}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s, b := PlanEntry(c.spec), PlanEntry(mirror(c.spec, c.obstacles))
			if len(s.Candidates) != len(b.Candidates) || s.Selected != b.Selected || s.Refusal != b.Refusal {
				t.Fatalf("supply %v sel %d / demand %v sel %d", kinds(s), s.Selected, kinds(b), b.Selected)
			}
			for i := range s.Candidates {
				x, y := s.Candidates[i], b.Candidates[i]
				if x.Kind != y.Kind || x.Feasible != y.Feasible || x.Rejected != y.Rejected || math.Abs(x.Price-(8600-y.Price)) > 1e-9 || math.Abs(x.RoomR-y.RoomR) > 1e-9 {
					t.Fatalf("candidate %d: %+v vs %+v", i, x, y)
				}
			}
		})
	}
}

func TestNoBudgetStillPlansAndANoObstacleZoneUsesTheFallbackTarget(t *testing.T) {
	spec := sellSpec(4300, 4304.5, 4305)
	spec.Budget = StopBudget{}
	spec.MinBandWidth = 0
	spec.Obstacles = below()
	c, ok := PlanEntry(spec).Chosen()
	if !ok || c.Kind != EntryProximal || c.TargetLabel != "fixed_reward_risk" || math.Abs(c.RoomR-2.0) > 1e-9 {
		t.Fatalf("with nothing in the way the target is the fixed reward:risk: %+v", c)
	}
}

// EURUSD-like: pip 0.0001, tick 0.00001, floor 5 pips, cap 20 pips, no minimum entry width. The same planner, the instrument's own precision.
func TestFXEntryUsesTheInstrumentsOwnPipAndTick(t *testing.T) {
	spec := EntrySpec{
		Sell: true, StructLow: 1.11000, StructHigh: 1.11120, Invalidation: 1.11300, MaxBandWidth: 0.0008, Budget: StopBudget{MaxPips: 20, MinPips: 5, PipSize: 0.0001},
		MinimumTargetR: 1.0, FallbackTargetR: 2.0, Tick: 0.00001, Obstacles: below(1.10500),
	}
	plan := PlanEntry(spec)
	c, ok := plan.Chosen()
	if !ok {
		t.Fatalf("no entry: %v refusal %q", kinds(plan), plan.Refusal)
	}
	// 30 pips from the proximal edge is beyond the 20-pip cap, so the entry goes in to the price that risks exactly 20 pips: a multiple of the tick.
	if c.Kind != EntryStopCapFit || math.Abs(c.StopPips-20) > 1e-6 {
		t.Fatalf("chosen %+v", c)
	}
	if ticks := c.Price / 0.00001; math.Abs(ticks-math.Round(ticks)) > 1e-6 {
		t.Fatalf("%v is not on the instrument's tick", c.Price)
	}
	if c.Price < spec.StructLow || c.Price > spec.StructHigh || c.BandLow != c.Price || c.BandHigh > spec.StructHigh+1e-12 {
		t.Fatalf("the entry and its band stay inside the zone: %+v", c)
	}
	if math.Abs(c.EffectiveRisk-0.0020) > 1e-9 {
		t.Fatalf("effective risk in price units: %v", c.EffectiveRisk)
	}
}

// The proximal edge and the base edge are bars' own prices; the midpoint and the deep price are computed. All of them are put on the tick (toward the inside of the zone) so the
// published band starts exactly at the recommended price.
func TestEveryCandidateIsOnTheInstrumentsTick(t *testing.T) {
	spec := EntrySpec{
		Sell: true, StructLow: 1.347183, StructHigh: 1.348637, BodyEdge: 1.347196, Invalidation: 1.35, MaxBandWidth: 0.0008, Budget: StopBudget{MaxPips: 40, MinPips: 5, PipSize: 0.0001},
		MinimumTargetR: 1.0, FallbackTargetR: 2.0, Tick: 0.00001, Obstacles: below(1.34),
	}
	plan := PlanEntry(spec)
	if len(plan.Candidates) < 3 {
		t.Fatalf("candidates %v", kinds(plan))
	}
	for _, c := range plan.Candidates {
		if ticks := c.Price / 0.00001; math.Abs(ticks-math.Round(ticks)) > 1e-6 {
			t.Fatalf("%s at %v is not on the tick", c.Kind, c.Price)
		}
		if c.Price < spec.StructLow-1e-12 || c.Price > spec.StructHigh+1e-12 {
			t.Fatalf("%s at %v left the zone", c.Kind, c.Price)
		}
	}
	if c, ok := plan.Chosen(); !ok || c.BandLow != c.Price {
		t.Fatalf("the band starts at the recommended price: %+v", c)
	}
}
