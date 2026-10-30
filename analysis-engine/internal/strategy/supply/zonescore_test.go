package supply

import "testing"

func inputsWithRange(high, low float64) scoreInputs {
	return scoreInputs{pd: dealingBand{High: high, Low: low, OK: true}, pdScore: 2, roundStep: 1000, pip: 0.01}
}

// The premium points go to a supply zone wholly in the upper half of the dealing range; a zone straddling the equilibrium, or in the lower half, gets none.
func TestOnlyASupplyZoneInThePremiumHalfEarnsThePointsAndTheyAreExactlyTheConfiguredValue(t *testing.T) {
	in := inputsWithRange(30, 15) // equilibrium 22.5
	base, premium := scoreZone(zone{Low: 28, High: 29, Touches: 2}, noRange(in))
	if premium {
		t.Fatal("without a range there are no premium points")
	}
	for _, c := range []struct {
		name      string
		z         zone
		premium   bool
		pdPoints  float64
		withRange bool
	}{
		{"wholly above the equilibrium", zone{Low: 28, High: 29, Touches: 2}, true, 2, true},
		{"straddling it", zone{Low: 20, High: 25, Touches: 2}, false, 0, true},
		{"below it", zone{Low: 16, High: 18, Touches: 2}, false, 0, true},
		{"starting exactly at it", zone{Low: 22.5, High: 24, Touches: 2}, true, 2, true},
	} {
		got, premium := scoreZone(c.z, in)
		want, _ := scoreZone(c.z, noRange(in))
		if premium != c.premium || got-want != c.pdPoints {
			t.Errorf("%s: premium=%v points=%v, want %v / %v", c.name, premium, got-want, c.premium, c.pdPoints)
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
	z := zone{Low: 28, High: 29, Touches: 2}
	without, _ := scoreZone(z, in)
	in.higher = []band{{Low: 27, High: 31}}
	with, _ := scoreZone(z, in)
	if with-without != 3 {
		t.Fatalf("a zone inside a higher-frame zone earns %v points, want 3", with-without)
	}
	in.higher = []band{{Low: 28.5, High: 31}} // only overlapping
	if partial, _ := scoreZone(z, in); partial != without {
		t.Fatalf("a zone only overlapping a higher-frame zone earns nothing: %v vs %v", partial, without)
	}
}
