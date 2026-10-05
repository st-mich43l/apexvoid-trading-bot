package techniquezone

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Reference values come from CPython 3.12 / pandas_ta 0.4.71b0, the runtime the
// frozen detectors ran on.

func TestPySumMatchesCPythonCompensatedSum(t *testing.T) {
	tenth := make([]float64, 10)
	for i := range tenth {
		tenth[i] = 0.1
	}
	if got := pySum(tenth...); got != 1.0 {
		t.Fatalf("sum of ten 0.1 = %v, CPython 3.12 gives exactly 1.0", got)
	}
	naive := 0.0
	for _, v := range tenth {
		naive += v
	}
	if naive == 1.0 {
		t.Fatal("fixture is meaningless: a naive loop already gives 1.0")
	}
	if got := pySum(1e100, 1.0, -1e100); got != 1.0 {
		t.Fatalf("compensation must survive cancellation, got %v", got)
	}
	prices := []float64{158.343, 158.356, 158.363}
	if got := pySum(prices...); got != 475.062 {
		t.Fatalf("cluster sum = %v, want 475.062", got)
	}
	if got := pySum(prices...) / 3; got != 158.354 {
		t.Fatalf("cluster centre = %v, want exactly 158.354", got)
	}
	// This centre decides whether 158.374 joins the cluster at tolerance 0.02.
	if math.Abs(158.374-pySum(prices...)/3) > 0.02 {
		t.Fatal("158.374 must be within tolerance of the compensated centre")
	}
	if pySum() != 0 {
		t.Fatal("empty sum is zero")
	}
}

func TestPythonRoundRoundsTheExactBinaryValue(t *testing.T) {
	for _, tc := range []struct {
		in     float64
		digits int
		want   float64
	}{{6.9875, 3, 6.987}, {2.675, 2, 2.67}, {9.55, 3, 9.55}, {0.5, 0, 0}, {1.5, 0, 2}, {2.5, 0, 2}} {
		if got := pythonRound(tc.in, tc.digits); got != tc.want {
			t.Fatalf("round(%v, %d) = %v, want %v", tc.in, tc.digits, got, tc.want)
		}
	}
}

func referenceBars() []market.Candle {
	rows := [][4]float64{{100.0, 100.621, 99.604, 100.266}, {100.266, 100.56, 99.369, 99.805}, {99.805, 100.08, 98.945, 98.988}, {98.988, 99.751, 98.697, 98.793}, {98.793, 99.544, 97.518, 97.769}, {97.769, 98.259, 96.753, 98.18}, {98.18, 99.13, 97.464, 99.091}, {99.091, 99.351, 97.775, 98.231}, {98.231, 98.619, 97.878, 98.107}, {98.107, 99.368, 97.672, 98.658}, {98.658, 99.689, 97.974, 98.913}, {98.913, 99.195, 98.636, 98.751}, {98.751, 99.835, 97.995, 98.573}, {98.573, 99.534, 97.784, 98.309}, {98.309, 99.541, 97.2, 97.771}, {97.771, 98.718, 96.889, 98.49}, {98.49, 99.638, 97.915, 99.604}, {99.604, 99.957, 98.86, 99.691}, {99.691, 100.089, 98.855, 98.904}, {98.904, 99.972, 97.71, 99.006}, {99.006, 100.344, 98.398, 99.751}, {99.751, 100.724, 98.798, 99.676}, {99.676, 100.968, 98.248, 99.538}, {99.538, 100.601, 99.259, 100.201}, {100.201, 101.242, 98.71, 100.791}, {100.791, 101.361, 100.089, 100.94}, {100.94, 101.169, 100.139, 100.312}, {100.312, 100.665, 100.036, 100.519}, {100.519, 100.887, 99.997, 100.345}, {100.345, 101.678, 100.04, 100.776}, {100.776, 101.69, 99.427, 101.281}, {101.281, 102.604, 100.719, 101.502}, {101.502, 102.169, 100.153, 102.083}, {102.083, 102.48, 101.654, 101.846}, {101.846, 102.349, 101.015, 101.801}, {101.801, 102.343, 101.596, 101.909}, {101.909, 102.589, 100.972, 102.513}, {102.513, 103.61, 101.643, 102.858}, {102.858, 103.937, 102.588, 103.801}, {103.801, 105.015, 102.465, 104.5}}
	bars := make([]market.Candle, len(rows))
	for i, row := range rows {
		bars[i] = market.Candle{Time: int64(i) * 300, Open: row[0], High: row[1], Low: row[2], Close: row[3]}
	}
	return bars
}

func TestDetectorATRMatchesPandasTA(t *testing.T) {
	bars := referenceBars()
	for _, tc := range []struct {
		n    int
		want float64
	}{{38, 1.5315984769833715}, {39, 1.5185557286274165}, {40, 1.5922303194397438}} {
		if got := DetectorATR(bars[:tc.n], 14, 1); math.Abs(got-tc.want) > 1e-12 {
			t.Fatalf("ATR over %d bars = %v, pandas_ta gives %v", tc.n, got, tc.want)
		}
	}
	if got := DetectorATR(bars[:13], 14, 1); got != 1 {
		t.Fatalf("a cold series falls back, got %v", got)
	}
	// It is deliberately not the simple rolling-mean ATR the analysis steps use.
	simple := ATRSeries(bars, 14)
	if math.Abs(simple[len(simple)-1]-DetectorATR(bars, 14, 1)) < 1e-6 {
		t.Fatal("detector ATR must differ from the simple ATR series")
	}
}
