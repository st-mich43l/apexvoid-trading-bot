package czreplay_test

import (
	"encoding/json"
	"os"
	"runtime"
	"sort"
	"sync"
	"testing"
)

// Variant is one coefficient moved, all others as configured.
type Variant struct {
	Name      string         `json:"name"`
	Overrides map[string]any `json:"overrides"`
}

var variants = []Variant{
	{"configured", nil},
	{"minimum_facts 3", map[string]any{"minimum_facts": 3.0}},
	{"minimum_overlap_atr 0 and pips 0.1", map[string]any{"minimum_overlap_atr": 0.0, "minimum_overlap_pips": 0.1}},
	{"minimum_overlap_atr 0.15", map[string]any{"minimum_overlap_atr": 0.15}},
	{"rejection_wick_ratio 0.15", map[string]any{"rejection_wick_ratio": 0.15}},
	{"rejection_wick_ratio 0.40", map[string]any{"rejection_wick_ratio": 0.40}},
	{"rejection_close_strength 0.50", map[string]any{"rejection_close_strength": 0.50}},
	{"rejection_close_strength 0.70", map[string]any{"rejection_close_strength": 0.70}},
	{"minimum_reclaim_atr 0.10", map[string]any{"minimum_reclaim_atr": 0.10}},
	{"displacement_body_ratio 0.5 and range 0.4", map[string]any{"displacement_body_ratio": 0.5, "displacement_range_atr": 0.4}},
	{"invalidation_buffer_atr 0.25", map[string]any{"invalidation_buffer_atr": 0.25}},
	{"invalidation_buffer_atr 0.75", map[string]any{"invalidation_buffer_atr": 0.75}},
	{"minimum_reward_risk 1.0", map[string]any{"minimum_reward_risk": 1.0}},
	{"minimum_reward_risk 1.5", map[string]any{"minimum_reward_risk": 1.5}},
	{"minimum_target_distance_atr 0.5", map[string]any{"minimum_target_distance_atr": 0.5}},
	{"minimum_target_distance_atr 1.5", map[string]any{"minimum_target_distance_atr": 1.5}},
}

// VariantPeriod is model D under one variant over one period.
type VariantPeriod struct {
	Decisions int  `json:"decisions"`
	Thinned   int  `json:"thinned"`
	Filled    int  `json:"filled_executable"`
	Single    Stat `json:"single_target_r"`
	Ladder    Stat `json:"ladder_r"`
}

// VariantResult is one variant over every period, and which of the four frozen-regime
// XAU production trades it would have published.
type VariantResult struct {
	Variant
	Periods                  map[string]VariantPeriod `json:"periods"`
	PublishesOfTheFourFrozen []string                 `json:"publishes_of_the_four_frozen_regime_xau_trades"`
}

type Sensitivity struct {
	Description string          `json:"description"`
	Variants    []VariantResult `json:"variants"`
}

func summarizeVariant(rs map[string]*Run) (map[string]VariantPeriod, []string) {
	out := map[string]VariantPeriod{}
	for _, p := range periods {
		ds := decisionsOf(runsFor(rs, p), "D", p)
		th := thin(append([]*Decision(nil), ds...))
		var single, ladder []float64
		for _, d := range th {
			if d.Single.Result == "unfilled" || d.Single.Result == "invalid" || d.Single.Result == "" || !d.Executable {
				continue
			}
			single = append(single, d.Single.R)
			ladder = append(ladder, d.Ladder.R)
		}
		vp := VariantPeriod{Decisions: len(ds), Thinned: len(th), Filled: len(single), Single: stat(single), Ladder: stat(ladder)}
		for _, st := range []*Stat{&vp.Single, &vp.Ladder} {
			st.Mean, st.Lo, st.Hi = round(st.Mean, 3), round(st.Lo, 3), round(st.Hi, 3)
		}
		out[p.Key] = vp
	}
	return out, nil
}

func frozenXAUTradesPublished(t testing.TB, r *Run) []string {
	var out []string
	for _, tr := range loadLedger(t) {
		if tr.Symbol != "XAU" || tr.Regime != "frozen_python_publisher_union" {
			continue
		}
		confirm := utc(tr.AEConfirm[:16])
		for _, d := range r.Decisions["D"] {
			if d.Direction == tr.Direction && abs64(d.ConfirmTime-confirm) <= 900 && overlapPositive(d.EntryLow, d.EntryHigh, tr.Entry[0], tr.Entry[1]) {
				out = append(out, tr.CloseTime[5:16]+" "+tr.Direction)
				break
			}
		}
	}
	return out
}

// TestSensitivity (CZ_SENSITIVITY_OUT) moves one coefficient at a time and replays
// every capture with the v3 strategy alone, to show how much the result depends on
// the configured values. It is not run by default: it replays every capture once
// per variant.
func TestSensitivity(t *testing.T) {
	out := os.Getenv("CZ_SENSITIVITY_OUT")
	if out == "" {
		t.Skip("CZ_SENSITIVITY_OUT not set")
	}
	type job struct {
		vi int
		c  Capture
	}
	var jobs []job
	for vi := range variants {
		for _, c := range captures {
			jobs = append(jobs, job{vi, c})
		}
	}
	results := make([][]*Run, len(variants))
	for i := range results {
		results[i] = make([]*Run, len(captures))
	}
	var wg sync.WaitGroup
	queue := make(chan job)
	for w := 0; w < runtime.NumCPU(); w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := range queue {
				r := doReplay(t, j.c, replayOpts{overrides: variants[j.vi].Overrides, light: true})
				for ci, c := range captures {
					if c.File == j.c.File {
						results[j.vi][ci] = r
					}
				}
			}
		}()
	}
	for _, j := range jobs {
		queue <- j
	}
	close(queue)
	wg.Wait()
	s := Sensitivity{Description: "Model D under one coefficient moved at a time (all others as configured), light replays of every capture. Not a search: the configured values are the shared v3 conventions; this shows how much the result depends on them."}
	for vi, v := range variants {
		rs := map[string]*Run{}
		for ci, c := range captures {
			rs[c.Name] = results[vi][ci]
		}
		periodsOut, _ := summarizeVariant(rs)
		s.Variants = append(s.Variants, VariantResult{Variant: v, Periods: periodsOut, PublishesOfTheFourFrozen: frozenXAUTradesPublished(t, rs["XAU Oct"])})
	}
	sort.SliceStable(s.Variants, func(i, j int) bool { return false })
	raw, _ := json.MarshalIndent(s, "", " ")
	if err := os.WriteFile(out, append(raw, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
}

// The committed sensitivity's "configured" row is the same v3 strategy as the
// certification's model D, replayed in light mode: the two must agree exactly. A
// cheap, always-run check that the committed evidence is internally consistent.
func TestCommittedSensitivityAgreesWithTheCertification(t *testing.T) {
	var sens Sensitivity
	raw, err := os.ReadFile(repoFile("docs", "strategies", "confluence-zone-v3-sensitivity.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &sens); err != nil {
		t.Fatal(err)
	}
	var cert Results
	raw, err = os.ReadFile(repoFile("docs", "strategies", "confluence-zone-v3-replay-results.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &cert); err != nil {
		t.Fatal(err)
	}
	var configured *VariantResult
	for i := range sens.Variants {
		if sens.Variants[i].Name == "configured" {
			configured = &sens.Variants[i]
		}
	}
	if configured == nil || len(sens.Variants) != len(variants) {
		t.Fatalf("the committed sensitivity does not hold the %d variants", len(variants))
	}
	for key, vp := range configured.Periods {
		d := cert.Periods[key].Models["D"]
		if vp.Decisions != d.Raw || vp.Thinned != d.Thinned || vp.Filled != d.Filled || vp.Single != d.Single || vp.Ladder != d.Ladder {
			t.Errorf("%s: the configured sensitivity row %+v differs from the certification's model D %+v", key, vp, d)
		}
	}
}
