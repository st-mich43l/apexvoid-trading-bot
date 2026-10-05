package techniquezone

import "math"

// pySum mirrors CPython 3.12's built-in sum() over floats: Neumaier's improved
// Kahan-Babuska compensated summation. The frozen analysis averages swing and
// contact prices with sum(...)/len(...), and a naive loop can differ from it by
// an ulp, which flips comparisons that sit exactly on a tolerance boundary.
func pySum(values ...float64) float64 {
	result, compensation := 0.0, 0.0
	for _, x := range values {
		t := result + x
		if math.Abs(result) >= math.Abs(x) {
			compensation += (result - t) + x
		} else {
			compensation += (x - t) + result
		}
		result = t
	}
	if compensation != 0 && !math.IsInf(compensation, 0) && !math.IsNaN(compensation) {
		result += compensation
	}
	return result
}
