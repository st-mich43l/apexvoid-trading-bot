package scalpparity_test

import (
	"fmt"
	"testing"
)

// TestRangeSweepMatchesTheProfitableWeekPython replays the real XAU capture that
// carries M1 bars through the Go engine and requires the opportunities of the
// Python discover_range_sweep of the profitable week on the same bars.
func TestRangeSweepMatchesTheProfitableWeekPython(t *testing.T) {
	var want struct {
		Capture       string            `json:"capture"`
		Cycles        []int64           `json:"cycles"`
		Opportunities []laneOpportunity `json:"opportunities"`
	}
	readJSON(t, "range-sweep-oracle.json", &want)
	got := replayLane(t, want.Capture, "range_sweep")
	matched := compareLane(t, "range_sweep", goldenLane{Cycles: want.Cycles, Opportunities: want.Opportunities}, got, 0.01, false)
	if matched != len(want.Opportunities) || len(want.Opportunities) < 3 {
		t.Fatalf("matched %d of %d Python opportunities", matched, len(want.Opportunities))
	}
}

// TestImpulsePullbackMatchesTheProfitableWeekPython is the same comparison for the
// Python discover_impulse_pullback of the profitable week.
func TestImpulsePullbackMatchesTheProfitableWeekPython(t *testing.T) {
	var want struct {
		Capture       string            `json:"capture"`
		Cycles        []int64           `json:"cycles"`
		Opportunities []laneOpportunity `json:"opportunities"`
	}
	readJSON(t, "impulse-pullback-oracle.json", &want)
	got := replayLane(t, want.Capture, "impulse_pullback")
	matched := compareLane(t, "impulse_pullback", goldenLane{Cycles: want.Cycles, Opportunities: want.Opportunities}, got, 0.01, false)
	t.Logf("impulse_pullback: %d cycles, %d/%d Python opportunities", len(want.Cycles), matched, len(want.Opportunities))
	if matched != len(want.Opportunities) {
		t.Fatalf("matched %d of %d Python opportunities", matched, len(want.Opportunities))
	}
}

// The synthetic captures give both scalps far more opportunities than the real
// 33-hour window: a price process with range and trend regimes, run through the
// same Python lane and the same Go engine.
func TestScalpLaneMatchesPythonOnTheSyntheticCaptures(t *testing.T) {
	for _, seed := range []int{7, 11, 23} {
		var want struct {
			Capture     string            `json:"capture"`
			Cycles      []int64           `json:"cycles"`
			Breakouts   []laneOpportunity `json:"breakouts"`
			RangeSweeps []laneOpportunity `json:"range_sweeps"`
		}
		readJSON(t, fmt.Sprintf("synthetic-scalp-oracle-%d.json", seed), &want)
		sweeps := compareLane(t, fmt.Sprintf("synthetic %d range_sweep", seed),
			goldenLane{Cycles: want.Cycles, Opportunities: want.RangeSweeps}, replayLane(t, want.Capture, "range_sweep"), 0.01, false)
		breakouts := compareLane(t, fmt.Sprintf("synthetic %d breakout", seed),
			goldenLane{Cycles: want.Cycles, Opportunities: want.Breakouts}, replayLane(t, want.Capture, "scalp_breakout_retest"), 0.01, true)
		t.Logf("seed %d: %d cycles, range sweep %d/%d, breakout %d/%d", seed, len(want.Cycles), sweeps, len(want.RangeSweeps), breakouts, len(want.Breakouts))
		if sweeps != len(want.RangeSweeps) || breakouts != len(want.Breakouts) {
			t.Errorf("seed %d: matched range sweep %d/%d, breakout %d/%d", seed, sweeps, len(want.RangeSweeps), breakouts, len(want.Breakouts))
		}
	}
}

// TestImpulsePullbackMatchesPythonOnTheSyntheticCaptures replays nine seeded
// synthetic captures (18 Python Impulse Pullback decisions in all) through the Go
// engine: the same opportunities on the same bars and none elsewhere. The real
// capture holds no Python decision, so this is the only evidence of the port.
func TestImpulsePullbackMatchesPythonOnTheSyntheticCaptures(t *testing.T) {
	total := 0
	for _, seed := range []int{7, 11, 23, 31, 37, 41, 43, 47, 53} {
		var want struct {
			Capture string            `json:"capture"`
			Cycles  []int64           `json:"cycles"`
			Impulse []laneOpportunity `json:"impulse_pullbacks"`
		}
		readJSON(t, fmt.Sprintf("synthetic-scalp-oracle-%d.json", seed), &want)
		matched := compareLane(t, fmt.Sprintf("synthetic %d impulse", seed),
			goldenLane{Cycles: want.Cycles, Opportunities: want.Impulse}, replayLane(t, want.Capture, "impulse_pullback"), 0.01, false)
		t.Logf("seed %d: impulse pullback %d/%d", seed, matched, len(want.Impulse))
		if matched != len(want.Impulse) {
			t.Errorf("seed %d: matched impulse pullback %d/%d", seed, matched, len(want.Impulse))
		}
		total += len(want.Impulse)
	}
	if total < 10 {
		t.Fatalf("the synthetic goldens hold only %d impulse pullback decisions", total)
	}
}
