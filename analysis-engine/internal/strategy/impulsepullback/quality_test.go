package impulsepullback

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

func goodImpulse() qualityInputs {
	return qualityInputs{
		displacementATR: 3.6, minDisplacementATR: 3, bodyDominance: 0.85, minBodyDominance: 0.5, pullbackRatio: 0.2, maxPullbackRatio: 0.7,
		referenceTouches: 3, price: 100.4, reference: 100.3, atr: 1, rewardPips: 3.6, stop: 1.2,
	}
}

func scoreImpulse(t *testing.T, in qualityInputs) (float64, map[string]float64) {
	t.Helper()
	m := scoreQuality(in)
	if err := quality.Verify(m.Overall(), m.Components()); err != nil {
		t.Fatal(err)
	}
	return m.Overall(), m.Components()
}

func TestStrongMediumAndWeakImpulsePullbacksScoreDifferently(t *testing.T) {
	medium := goodImpulse()
	medium.displacementATR, medium.bodyDominance, medium.pullbackRatio, medium.referenceTouches = 3.4, 0.65, 0.45, 2
	medium.price, medium.rewardPips = 100.9, 2.4

	weak := goodImpulse()
	weak.displacementATR, weak.bodyDominance, weak.pullbackRatio, weak.referenceTouches = 3.0, 0.5, 0.69, 1 // barely qualified
	weak.price, weak.rewardPips = 101.6, 1.0

	s, _ := scoreImpulse(t, goodImpulse())
	m, _ := scoreImpulse(t, medium)
	w, wc := scoreImpulse(t, weak)
	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.8 || m < 0.25 || m > 0.75 || w > 0.3 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestAnExhaustedImpulseAndAWornReferenceScoreBelowTheirHealthyForms(t *testing.T) {
	healthy, spike, worn := goodImpulse(), goodImpulse(), goodImpulse()
	spike.displacementATR = 15
	worn.referenceTouches = 9
	_, h := scoreImpulse(t, healthy)
	_, s := scoreImpulse(t, spike)
	_, w := scoreImpulse(t, worn)
	if !(s["score_impulse_strength"] < h["score_impulse_strength"] && w["score_reference_quality"] < h["score_reference_quality"]) {
		t.Fatalf("bigger is not better: %v %v / %v %v", s["score_impulse_strength"], h["score_impulse_strength"], w["score_reference_quality"], h["score_reference_quality"])
	}
}

func TestImpulseScoreIsScaleFreeAndNeedsItsMeasurements(t *testing.T) {
	_, base := scoreImpulse(t, goodImpulse())
	k := 1000.0
	in := goodImpulse()
	in.price, in.reference, in.atr, in.rewardPips, in.stop = in.price*k, in.reference*k, in.atr*k, in.rewardPips*k, in.stop*k
	_, got := scoreImpulse(t, in)
	for _, name := range []string{"score_entry_location", "score_room"} {
		if math.Abs(got[name]-base[name]) > 1e-9 {
			t.Errorf("%s depends on the price scale", name)
		}
	}
	none := goodImpulse()
	none.atr, none.stop = 0, 0
	if _, c := scoreImpulse(t, none); c["score_entry_location"] != 0 || c["score_room"] != 0 {
		t.Fatalf("missing evidence scored: %v", c)
	}
}
