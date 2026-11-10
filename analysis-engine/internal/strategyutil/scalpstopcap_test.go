package strategyutil

import "testing"

// The shared scalp stop book: a structural stop is accepted only when it fits the cap, a narrow one is widened to the minimum, a wide one is refused and never compressed.
func TestScalpStopPipsHonoursTheFortyPipCap(t *testing.T) {
	const minimum, maximum = 30.0, 40.0
	cases := []struct {
		name       string
		structural float64
		want       float64
		ok         bool
	}{
		{"24 pips is widened to the 30 minimum", 24, 30, true},
		{"35 pips is kept", 35, 35, true},
		{"40 pips fits exactly", 40, 40, true},
		{"floating-point noise on the cap is not a breach", 40 + 1e-12, 40 + 1e-12, true},
		{"40.01 pips is refused", 40.01, 0, false},
		{"48 pips is refused, not compressed", 48, 0, false},
		{"a sell-side negative distance is measured by size", -35, 35, true},
		{"zero is refused", 0, 0, false},
	}
	for _, c := range cases {
		got, ok := ScalpStopPips(c.structural, minimum, maximum)
		if ok != c.ok || (ok && got != c.want) {
			t.Errorf("%s: ScalpStopPips(%v) = %v, %v; want %v, %v", c.name, c.structural, got, ok, c.want, c.ok)
		}
	}
}
