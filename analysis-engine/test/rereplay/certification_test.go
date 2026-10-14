package rereplay_test

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"testing"
)

// heldOut is the start of the chronological held-out period: nothing in v3 was chosen
// on, or after, it. The configured coefficients are shared conventions, not fitted.
const heldOut int64 = 1_791_590_400 // 2026-10-10 00:00 UTC

type scope struct {
	Name     string
	Captures []string
}

var scopes = []scope{
	{"USDJPY", []string{"USDJPY"}},
	{"FX_other (EURUSD, GBPUSD, GBPJPY)", []string{"EURUSD", "GBPUSD", "GBPJPY"}},
	{"FX_all", []string{"USDJPY", "EURUSD", "GBPUSD", "GBPJPY"}},
	{"XAU_Sep", []string{"XAU Sep"}},
	{"XAU_Oct", []string{"XAU Oct"}},
	{"XAU_all", []string{"XAU Sep", "XAU Oct"}},
}

// ModelResult is one model's decisions in one scope, valued causally.
type ModelResult struct {
	Decisions  int            `json:"decisions"`
	Thinned    int            `json:"thinned_one_per_symbol_direction_hour"`
	Ladder     Stat           `json:"ladder"`
	OneR       Stat           `json:"single_target_1R"`
	TwoR       Stat           `json:"single_target_2R"`
	DevLadder  Stat           `json:"development_ladder"`
	HeldLadder Stat           `json:"held_out_ladder"`
	MedianRisk float64        `json:"median_executed_risk_pips"`
	MedianRR   float64        `json:"median_reward_risk_to_equilibrium"`
	Duplicates int            `json:"same_range_duplicate_attempts"`
	Patterns   map[string]int `json:"patterns"`
	Structures map[string]int `json:"structure_kinds"`
	Results    map[string]int `json:"fill_results"`
}

func median(v []float64) float64 {
	if len(v) == 0 {
		return 0
	}
	sort.Float64s(v)
	return round(v[len(v)/2], 2)
}

// duplicates counts decisions that repeat an earlier one: same capture and direction, a
// different opportunity, within 90 minutes, with an entry band within two ATR of it.
func duplicates(ds []*Decision) int {
	n := 0
	for i, d := range ds {
		for j := 0; j < i; j++ {
			p := ds[j]
			if p.Capture == d.Capture && p.Direction == d.Direction && p.ID != d.ID && d.EvalTime-p.EvalTime <= 90*60 && d.EvalTime >= p.EvalTime &&
				absf(d.EntryLow-p.EntryLow) <= 2*d.ATR {
				n++
				break
			}
		}
	}
	return n
}

func absf(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func evaluateModel(runsByName map[string]*Run, decisionsByCapture map[string][]*Decision, sc scope) ModelResult {
	out := ModelResult{Patterns: map[string]int{}, Structures: map[string]int{}, Results: map[string]int{}}
	var ladder, one, two, dev, held []Outcome
	var risks, rrs []float64
	var all []*Decision
	for _, name := range sc.Captures {
		r := runsByName[name]
		ds := decisionsByCapture[name]
		out.Decisions += len(ds)
		all = append(all, ds...)
		for _, d := range ds {
			out.Patterns[d.Pattern]++
			kind := "initial"
			if strings.Contains(d.Structure, ":break:") {
				kind = "re-established after a break"
			}
			out.Structures[kind]++
		}
		thinned := thin(ds)
		out.Thinned += len(thinned)
		for _, d := range thinned {
			sched := scheduleFor(d.Symbol)
			o := simulate(r, d, sched)
			ladder = append(ladder, o)
			one = append(one, simulate(r, d, oneR))
			two = append(two, simulate(r, d, twoR))
			out.Results[o.Result]++
			if d.EvalTime >= heldOut {
				held = append(held, o)
			} else {
				dev = append(dev, o)
			}
			if o.Result != "unfilled" && o.Result != "not_executable" {
				risks = append(risks, o.RiskPips)
				if o.RiskPips > 0 {
					rrs = append(rrs, absf(d.Equilibrium-o.Fill)/r.Pip/o.RiskPips)
				}
			}
		}
	}
	sort.SliceStable(all, func(i, j int) bool { return all[i].EvalTime < all[j].EvalTime })
	out.Duplicates = duplicates(all)
	out.Ladder, out.OneR, out.TwoR = summarize(ladder, out.Thinned), summarize(one, out.Thinned), summarize(two, out.Thinned)
	out.DevLadder, out.HeldLadder = summarize(dev, len(dev)), summarize(held, len(held))
	out.MedianRisk, out.MedianRR = median(risks), median(rrs)
	return out
}

// Results is the committed machine-readable certification.
type Results struct {
	Description string                            `json:"description"`
	HeldOutFrom string                            `json:"held_out_from_utc"`
	Scopes      map[string]map[string]ModelResult `json:"scopes"`
	Comparison  map[string]Comparison             `json:"v2_against_v3"`
	Rejections  map[string]map[string]int         `json:"v3_rejections_by_capture"`
	Incident    []IncidentRow                     `json:"usdjpy_incident"`
	OthersSHA   map[string]string                 `json:"other_strategies_candidates"`
	Limits      []string                          `json:"limits"`
}

// Comparison classifies every v2 and v3 decision of one scope against the other model.
type Comparison struct {
	V2             int      `json:"v2_decisions"`
	V3             int      `json:"v3_decisions"`
	Preserved      int      `json:"v2_preserved_in_v3"`
	Removed        int      `json:"v2_removed_in_v3"`
	New            int      `json:"v3_without_a_v2_counterpart"`
	RemovedWinners int      `json:"removed_v2_winners_ladder"`
	RemovedLosers  int      `json:"removed_v2_losers_ladder"`
	RemovedUnfill  int      `json:"removed_v2_unfilled_or_not_executable"`
	RemovedMeanR   float64  `json:"removed_v2_mean_r_ladder"`
	NewDecisions   []string `json:"v3_new_decisions"`
}

// IncidentRow follows one USDJPY opportunity of 12-13 October through both models.
type IncidentRow struct {
	V2ID      string  `json:"v2_id"`
	Direction string  `json:"direction"`
	Eval      string  `json:"eval_utc"`
	Entry     string  `json:"entry"`
	V3        string  `json:"v3"`
	R         float64 `json:"v2_hypothetical_ladder_r"`
}

func key(d *Decision) string { return fmt.Sprintf("%s|%s|%d", d.Capture, d.Direction, d.ConfirmTime) }

func buildResults(t *testing.T) *Results {
	runsByName := map[string]*Run{}
	v3 := map[string][]*Decision{}
	for _, c := range captures {
		r := replay(t, c)
		runsByName[c.Name] = r
		v3[c.Name] = r.Decisions
	}
	v2 := readBaseline(t)
	res := &Results{
		Description: "Range Edge v2 (frozen baseline, master f18179f-era code reproduced at 80e1be21) against v3 over the same committed production captures, closed candles, causal hypothetical fills (no spread, slippage, commission, M1 trigger or broker behaviour).",
		HeldOutFrom: "2026-10-10T00:00:00Z",
		Scopes:      map[string]map[string]ModelResult{}, Comparison: map[string]Comparison{}, Rejections: map[string]map[string]int{}, OthersSHA: map[string]string{},
	}
	for _, sc := range scopes {
		res.Scopes[sc.Name] = map[string]ModelResult{"v2": evaluateModel(runsByName, v2, sc), "v3": evaluateModel(runsByName, v3, sc)}
		cmp := Comparison{}
		var removed []Outcome
		for _, name := range sc.Captures {
			have3 := map[string]bool{}
			for _, d := range v3[name] {
				have3[key(d)] = true
			}
			have2 := map[string]bool{}
			for _, d := range v2[name] {
				have2[key(d)] = true
				cmp.V2++
				if have3[key(d)] {
					cmp.Preserved++
					continue
				}
				cmp.Removed++
				o := simulate(runsByName[name], d, scheduleFor(d.Symbol))
				switch {
				case o.Result == "unfilled" || o.Result == "not_executable":
					cmp.RemovedUnfill++
				case o.R > 0:
					cmp.RemovedWinners++
					removed = append(removed, o)
				default:
					cmp.RemovedLosers++
					removed = append(removed, o)
				}
			}
			for _, d := range v3[name] {
				cmp.V3++
				if !have2[key(d)] {
					cmp.New++
					cmp.NewDecisions = append(cmp.NewDecisions, fmt.Sprintf("%s %s %s %s entry %.5f-%.5f (%s)", name, d.ID[:12], d.Direction, utc(d.EvalTime), d.EntryLow, d.EntryHigh, d.Structure))
				}
			}
		}
		cmp.RemovedMeanR = summarize(removed, 0).Mean
		res.Comparison[sc.Name] = cmp
	}
	for _, c := range captures {
		counts := map[string]int{}
		for _, rej := range runsByName[c.Name].Rejections {
			counts[rej.Reason]++
		}
		res.Rejections[c.Name] = counts
		sort.Strings(runsByName[c.Name].Others)
		sum := sha256.Sum256([]byte(strings.Join(runsByName[c.Name].Others, "\n")))
		res.OthersSHA[c.Name] = fmt.Sprintf("%d:%s", len(runsByName[c.Name].Others), hex.EncodeToString(sum[:]))
	}
	// The incident, trade by trade.
	have3 := map[string]*Decision{}
	for _, d := range v3["USDJPY"] {
		have3[key(d)] = d
	}
	for _, d := range v2["USDJPY"] {
		if d.EvalTime < 1_791_763_200 || d.EvalTime > 1_791_763_200+42*3600 {
			continue
		}
		row := IncidentRow{V2ID: d.ID[:12], Direction: d.Direction, Eval: utc(d.EvalTime), Entry: fmt.Sprintf("%.3f-%.3f", d.EntryLow, d.EntryHigh)}
		if p, ok := have3[key(d)]; ok {
			row.V3 = "published as " + p.ID[:12]
		} else {
			row.V3 = "not published"
		}
		row.R = simulate(runsByName["USDJPY"], d, fxLadder).R
		res.Incident = append(res.Incident, row)
	}
	res.Limits = []string{
		"Hypothetical, causal fills on closed M5 candles: no spread, slippage, commission, M1 trigger or broker behaviour; a stop and a target in one candle count as the stop.",
		"Executed risk is the published invalidation widened to the instrument's scalp floor (USDJPY 18 pips); risk beyond the instrument cap is not executable.",
		"v2 is reproduced from decisions the master engine published on these captures (testdata/range-edge-v2-baseline.json); v3 coefficients are shared conventions, none chosen on the held-out period.",
		"The captures cover 2-13 Oct (FX) and 14-21 Sep and 1-13 Oct (XAU): small samples; intervals treat thinned setups as independent, which is optimistic.",
		"Opportunities v3 refuses cannot be valued as trades; the replay measures what v2 would have done with the ones v3 removed.",
	}
	return res
}

func utc(ts int64) string {
	const layout = "2006-01-02 15:04"
	return timeUTC(ts).Format(layout)
}

// TestCertification recomputes the results from the captures and, when
// RE_RESULTS_OUT is set, writes them; otherwise it asserts the committed file is current.
func TestCertification(t *testing.T) {
	res := buildResults(t)
	raw, err := json.MarshalIndent(res, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	raw = append(raw, '\n')
	if out := os.Getenv("RE_RESULTS_OUT"); out != "" {
		if err := os.WriteFile(out, raw, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	committed, err := os.ReadFile(repoFile("docs", "strategies", "range-edge-v3-replay-results.json"))
	if err != nil {
		t.Fatalf("the committed results are missing: %v", err)
	}
	if string(committed) != string(raw) {
		t.Fatal("docs/strategies/range-edge-v3-replay-results.json is stale: regenerate with RE_RESULTS_OUT")
	}
}
