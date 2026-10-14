package rereplay_test

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func decisionByPrefix(ds []*Decision, prefix string) *Decision {
	for _, d := range ds {
		if strings.HasPrefix(d.ID, "opp_"+prefix) {
			return d
		}
	}
	return nil
}

// The v2 baseline is not a synthetic stand-in: it reproduces, id for id, the
// opportunities production published on 12 October.
func TestTheBaselineReproducesTheProductionIncident(t *testing.T) {
	v2 := readBaseline(t)["USDJPY"]
	for _, id := range []string{"4ac72614", "ab2e1a3a", "17d2b4fc", "f5cd83b5", "d6fe8047", "b7d810e8"} {
		d := decisionByPrefix(v2, id)
		if d == nil {
			t.Fatalf("production opportunity %s is not reproduced by the replay", id)
		}
		if d.Direction != "SELL" {
			t.Fatalf("%s: %+v", id, d)
		}
	}
}

// v3 keeps the first fades of the range and refuses the ones made after the market
// accepted through its top: the continuation of one advance, one rebuilt edge at a time.
func TestV3RefusesTheContinuationOfAnAcceptedBreakAndKeepsTheOriginalFade(t *testing.T) {
	v2 := readBaseline(t)["USDJPY"]
	v3 := replay(t, captures[0]).Decisions
	confirmed := func(ds []*Decision, id string) *Decision {
		d := decisionByPrefix(v2, id)
		for _, p := range ds {
			if p.Direction == d.Direction && p.ConfirmTime == d.ConfirmTime {
				return p
			}
		}
		return nil
	}
	for _, id := range []string{"17d2b4fc", "f5cd83b5", "d6fe8047"} {
		if p := confirmed(v3, id); p != nil {
			t.Errorf("v3 must refuse %s (an edge rebuilt above an accepted break): %+v", id, p)
		}
	}
	// Between the break (07:05) and the range the advance formed after it (the first fade
	// of which is 11:05) v2 published four SELLs and v3 none: the staircase is not traded.
	const from, to = 1_791_763_200 + 7*3600 + 300, 1_791_763_200 + 11*3600
	for _, d := range v3 {
		if d.Direction == "SELL" && d.EvalTime >= from && d.EvalTime < to {
			t.Errorf("v3 published a SELL while the advance climbed: %+v", d)
		}
	}
	// Before the break the range was intact and its fades stay: the first one is published.
	found := false
	for _, d := range v3 {
		if d.Direction == "SELL" && d.EvalTime >= 1_791_763_200+5*3600 && d.EvalTime < 1_791_763_200+6*3600 {
			found = true
		}
	}
	if !found {
		t.Error("the fade of the intact range at 05:55 must be published")
	}
	reasons := map[string]int{}
	for _, r := range replay(t, captures[0]).Rejections {
		reasons[r.Reason]++
	}
	if reasons[ReasonEdgeBreak] == 0 || reasons[ReasonNotReestablished] == 0 {
		t.Errorf("the refusals must be on record with their reasons: %v", reasons)
	}
}

// Every other strategy publishes exactly what it published on master.
func TestOtherStrategiesAreUntouched(t *testing.T) {
	raw, err := os.ReadFile(testdata("range-edge-others-baseline-master.json"))
	if err != nil {
		t.Fatal(err)
	}
	var want map[string]string
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatal(err)
	}
	got := buildResults(t).OthersSHA
	for name, sha := range want {
		if got[name] != sha {
			t.Errorf("%s: other strategies' candidates changed: %s, want %s", name, got[name], sha)
		}
	}
}
