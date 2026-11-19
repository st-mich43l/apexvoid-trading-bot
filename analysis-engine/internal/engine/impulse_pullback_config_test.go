package engine

import "testing"

// RUN-2: the shipped Impulse Pullback values (each was the next refusing gate on the production XAU M1 bars) and the dealing-range limits the Analysis Engine reads,
// which equal the Algo Bot's execution-time trend-pullback limit (entry_location.trend_pullback 0.70 / 0.30), so the two never disagree.
func TestImpulsePullbackShipsTheRun2Values(t *testing.T) {
	settings := settingsFor(t, copyConfig(t, "false"))
	p := strategyParams(t, settings, "impulse_pullback")
	want := map[string]float64{
		"impulse_displacement_atr_multiple": 3.0, "impulse_body_dominance": 0.3, "pullback_corrective_ratio": 0.85,
		"buy_maximum_position": 0.70, "sell_minimum_position": 0.30,
	}
	for key, value := range want {
		got, ok := p[key].(float64)
		if !ok || got != value {
			t.Errorf("%s = %#v, want %v", key, p[key], value)
		}
	}
}
