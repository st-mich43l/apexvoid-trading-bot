package techniquezone

import (
	"math"
	"reflect"
	"testing"
)

func TestScoreZoneAddsEveryConfluenceInPythonOrder(t *testing.T) {
	zone := newZone(99, 100.5, "demand", 3, "order_block")
	zone.BreakKind = "BOS"
	in := ScoreInputs{
		Levels:    []Level{{Price: 99.5, Band: .2}},
		Pools:     []Pool{{Side: "sell", Level: 98.9, Band: .1}},
		RoundStep: 5, HTFZones: []Zone{newZone(95, 105, "demand", 0, "order_block")},
		Sessions: []SessionRef{{Name: "NY_L", Price: 99.2}, {Name: "ASIA_H", Price: 99.3}, {Name: "PDL", Price: 99.1, Swept: true}},
		HasEq:    true, Equilibrium: 101,
		Grabs:      []Grab{{Grade: "A", Direction: "bull", Pool: Pool{Side: "sell", Level: 98.9, Band: .1}}},
		LineValues: []float64{99.8}, HasBarIndex: true, PipSize: .1,
	}
	scored := scoreZone(zone, in)
	want := []string{"fresh", "OB", "key level", "round 100", "liquidity pool", "NY_L", "discount", "sweep A", "HTF zone", "TL confluence"}
	if !reflect.DeepEqual(scored.ScoreReasons, want) {
		t.Fatalf("reasons = %v, want %v", scored.ScoreReasons, want)
	}
	if math.Abs(scored.Score-(3+3+2+1+2+2+2+2+3+1.5)) > 1e-9 {
		t.Fatalf("score = %v, want 21.5", scored.Score)
	}
}

func TestScoreZoneDiscountsSpentAndInducementCases(t *testing.T) {
	zone := newZone(99, 100, "supply", 3, "supply_demand")
	zone.Touches = 1
	scored := scoreZone(zone, ScoreInputs{HasEq: true, Equilibrium: 98})
	if scored.Score != 1+1.5+2 || scored.ScoreReasons[0] != "1 touch" {
		t.Fatalf("one touch + S/D + premium = 4.5, got %v %v", scored.Score, scored.ScoreReasons)
	}
	zone.Touches = 2
	if got := scoreZone(zone, ScoreInputs{}); got.Score != 1.5 {
		t.Fatalf("a twice-touched zone only keeps its source score, got %v", got.Score)
	}
	// An unbroken order block has no source value, and a swept session level
	// or an inducement grab adds nothing.
	ob := newZone(99, 100, "demand", 3, "order_block")
	grabs := []Grab{{Grade: "A", Direction: "bull", Inducement: true, Pool: Pool{Side: "sell", Level: 98.9, Band: .1}}}
	got := scoreZone(ob, ScoreInputs{Grabs: grabs, Sessions: []SessionRef{{Name: "NY_L", Price: 99.5, Swept: true}}, PipSize: .1})
	if got.Score != 3 { // fresh only
		t.Fatalf("unbroken OB, swept session and inducement grab score nothing, got %v %v", got.Score, got.ScoreReasons)
	}
}

func TestScoreZonesOrderingIsScoreThenFewerTouchesThenLowerBand(t *testing.T) {
	a := newZone(100, 101, "demand", 1, "supply_demand")
	b := newZone(90, 91, "demand", 2, "supply_demand")
	c := newZone(95, 96, "demand", 3, "supply_demand")
	c.Touches = 1
	scored := ScoreZones([]Zone{a, b, c}, ScoreInputs{})
	// a and b tie at 4.5 (fresh + S/D): the lower band comes first; c has 1+1.5.
	if scored[0].Low() != 90 || scored[1].Low() != 100 || scored[2].Low() != 95 {
		t.Fatalf("unexpected order: %v %v %v", scored[0].Low(), scored[1].Low(), scored[2].Low())
	}
}
