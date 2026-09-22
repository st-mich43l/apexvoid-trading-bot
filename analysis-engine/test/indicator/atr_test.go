package indicator_test

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
)

// Hand-derived cases mirroring app/analysis/math_utils.py's own docstring
// behavior for atr_at/atr_scalar (tests/test_analysis_toolkit.py doesn't
// cover these directly at time of writing — these are new coverage, not a
// port of an existing Python test).

func TestAtrAt(t *testing.T) {
	series := []float64{1.0, 2.0, 3.0}
	cases := []struct {
		name     string
		series   []float64
		index    int
		fallback float64
		want     float64
	}{
		{"empty falls back", nil, 0, 9.0, 9.0},
		{"in range", series, 1, 9.0, 2.0},
		{"negative index clamps to 0", series, -5, 9.0, 1.0},
		{"over-range index clamps to last", series, 50, 9.0, 3.0},
		{"NaN falls back", []float64{math.NaN()}, 0, 9.0, 9.0},
		{"zero falls back", []float64{0.0}, 0, 9.0, 9.0},
		{"negative falls back", []float64{-1.0}, 0, 9.0, 9.0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := indicator.AtrAt(c.series, c.index, c.fallback)
			if got != c.want {
				t.Errorf("got %v want %v", got, c.want)
			}
		})
	}
}

func TestAtrScalar(t *testing.T) {
	cases := []struct {
		name     string
		series   []float64
		fallback float64
		want     float64
	}{
		{"empty falls back", nil, 9.0, 9.0},
		{"all NaN falls back", []float64{math.NaN(), math.NaN()}, 9.0, 9.0},
		{"odd length median", []float64{3.0, 1.0, 2.0}, 9.0, 2.0},
		{"even length median averages middle two", []float64{1.0, 2.0, 3.0, 4.0}, 9.0, 2.5},
		{"NaN dropped before median", []float64{1.0, math.NaN(), 3.0}, 9.0, 2.0},
		{"non-positive median falls back", []float64{-1.0, -2.0, -3.0}, 9.0, 9.0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := indicator.AtrScalar(c.series, c.fallback)
			if got != c.want {
				t.Errorf("got %v want %v", got, c.want)
			}
		})
	}
}
