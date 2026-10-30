package demand

import "testing"

func inputsWithRange(high, low float64) scoreInputs {
	return scoreInputs{pd: dealingBand{High: high, Low: low, OK: true}, pdScore: 2, roundStep: 1000, pip: 0.01}
}

// The discount points go to a demand zone wholly in the lower half of the dealing range; a zone straddling the equilibrium, or in the upper half, gets none.
func TestOnlyASupplyZoneInTheDiscountHalfEarnsThePointsAndTheyAreExactlyTheConfiguredValue(t *testing.T) {
	in := inputsWithRange(30, 15) // equilibrium 22.5
	base, discount := scoreZone(zone{Low: 16, High: 18, Touches: 2}, noRange(in))
	if discount {
		t.Fatal("without a range there are no discount points")
	}
	for _, c := range []struct {
		name      string
		z         zone
		discount  bool
		pdPoints  float64
		withRange bool
	}{
		{"wholly below the equilibrium", zone{Low: 16, High: 18, Touches: 2}, true, 2, true},
		{"straddling it", zone{Low: 20, High: 25, Touches: 2}, false, 0, true},
		{"above it", zone{Low: 28, High: 29, Touches: 2}, false, 0, true},
		{"ending exactly at it", zone{Low: 21, High: 22.5, Touches: 2}, true, 2, true},
	} {
		got, discount := scoreZone(c.z, in)
		want, _ := scoreZone(c.z, noRange(in))
		if discount != c.discount || got-want != c.pdPoints {
			t.Errorf("%s: discount=%v points=%v, want %v / %v", c.name, discount, got-want, c.discount, c.pdPoints)
		}
	}
	_ = base
}

func noRange(in scoreInputs) scoreInputs {
	in.pd = dealingBand{}
	return in
}

func TestAHigherTimeframeZoneOfThisStrategyAddsThreePoints(t *testing.T) {
	in := inputsWithRange(30, 15)
	z := zone{Low: 16, High: 18, Touches: 2}
	without, _ := scoreZone(z, in)
	in.higher = []band{{Low: 15, High: 19}}
	with, _ := scoreZone(z, in)
	if with-without != 3 {
		t.Fatalf("a zone inside a higher-frame zone earns %v points, want 3", with-without)
	}
	in.higher = []band{{Low: 17, High: 19}} // only overlapping
	if partial, _ := scoreZone(z, in); partial != without {
		t.Fatalf("a zone only overlapping a higher-frame zone earns nothing: %v vs %v", partial, without)
	}
}
