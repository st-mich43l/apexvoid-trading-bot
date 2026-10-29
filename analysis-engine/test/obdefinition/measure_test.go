package obdefinition_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"
)

// reference is one of the production trades the definition is judged on: the block it traded and when it was filled. Fills are from auto_trade_fills.
type reference struct {
	Name         string `json:"name"`
	Symbol       string `json:"symbol"`
	StructuralID string `json:"structural_id"`
	FilledAt     int64  `json:"filled_at"`
	Result       string `json:"result"`
	MustPublish  bool   `json:"must_publish"`
}

func at(day, hour, minute, second int) int64 {
	return time.Date(2026, 10, day, hour, minute, second, 0, time.UTC).Unix()
}

var references = []reference{
	{"XAU SELL 28 Oct 19:49 (published 01:20, waited 17 h)", "XAU", "technique:order_block:sell:1793122200", at(28, 19, 49, 28), "-48 pips", false},
	{"XAU BUY 28 Oct 23:51", "XAU", "technique:order_block:buy:1793216100", at(28, 23, 51, 5), "-30 pips", false},
	{"XAU SELL 29 Oct 01:47", "XAU", "technique:order_block:sell:1793231400", at(29, 1, 47, 4), "-51 pips", false},
	{"EURUSD BUY 28 Oct 18:31", "EURUSD", "technique:order_block:buy:1793202300", at(28, 18, 31, 4), "+24 pips", true},
}

type cell struct {
	Published         int            `json:"published"`
	BaselinePublished int            `json:"baseline_published"`
	Refused           int            `json:"refused"`
	RefusedByReason   map[string]int `json:"refused_by_reason"`
	RetiredByRuleC    int            `json:"retired_by_rule_c"`
	NewlyReachable    int            `json:"newly_reachable"`
}

type fate struct {
	Reference  string `json:"reference"`
	Fate       string `json:"fate"`
	LiveAtFill bool   `json:"live_at_fill"`
	Pass       bool   `json:"pass"`
}

type variantResult struct {
	Variant     variant                     `json:"variant"`
	Name        string                      `json:"name"`
	Cells       map[string]map[string]*cell `json:"by_symbol_and_structure_timeframe"`
	Totals      cell                        `json:"totals"`
	References  []fate                      `json:"reference_trades"`
	PassesAll   bool                        `json:"all_reference_criteria_pass"`
	LosersFixed bool                        `json:"the_three_losers_are_refused_or_retired_before_their_fill"`
	WinnerKept  bool                        `json:"the_eurusd_winner_still_publishes"`
}

func newCell() *cell { return &cell{RefusedByReason: map[string]int{}} }

// TestMeasureOrderBlockDefinition replays every committed M5 capture and the two production windows once per variant of the definition and writes what each
// variant publishes, refuses (by reason) and retires, and what happens to the four reference trades.
//
//	OB_DEFINITION_OUT=/tmp/ob go test ./test/obdefinition -run TestMeasureOrderBlockDefinition -timeout 60m
func TestMeasureOrderBlockDefinition(t *testing.T) {
	dir := os.Getenv("OB_DEFINITION_OUT")
	if dir == "" {
		t.Skip("OB_DEFINITION_OUT not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc := loadDoc(t)
	paths := captureNames(t)
	var variants []variant
	for _, buffer := range []float64{0, 0.1, 0.25, 1.0} {
		for _, touches := range []int{0, 2} {
			variants = append(variants, variant{buffer, touches})
		}
	}
	results := make([][]*replayResult, len(variants))
	for i := range results {
		results[i] = make([]*replayResult, len(paths))
	}
	var wg sync.WaitGroup
	gate := make(chan struct{}, runtime.NumCPU())
	for vi := range variants {
		for pi := range paths {
			wg.Add(1)
			go func(vi, pi int) {
				defer wg.Done()
				gate <- struct{}{}
				defer func() { <-gate }()
				results[vi][pi] = replayOB(t, doc, paths[pi], false, variants[vi])
			}(vi, pi)
		}
	}
	wg.Wait()
	base := results[0] // {0, 0}: the frozen behaviour

	var out []variantResult
	for vi, v := range variants {
		vr := variantResult{Variant: v, Name: v.name(), Cells: map[string]map[string]*cell{}}
		at := func(symbol string, tf string) *cell {
			if vr.Cells[symbol] == nil {
				vr.Cells[symbol] = map[string]*cell{}
			}
			if vr.Cells[symbol][tf] == nil {
				vr.Cells[symbol][tf] = newCell()
			}
			return vr.Cells[symbol][tf]
		}
		baseFirst, varFirst := map[string]sighting{}, map[string]sighting{}
		baseCapture := map[string]*replayResult{}
		retired := map[string]withdrawal{}
		for pi := range paths {
			b, r := base[pi], results[vi][pi]
			// Every other strategy publishes exactly what it published without the definition.
			if r.OthersSHA != b.OthersSHA || r.Others != b.Others {
				t.Errorf("%s %s: strategies other than order_block changed (%d -> %d candidates)", r.Capture, v.name(), b.Others, r.Others)
			}
			for id, s := range b.First {
				if _, seen := baseFirst[id]; !seen {
					baseFirst[id] = s
					baseCapture[id] = b
				}
			}
			for id, s := range r.First {
				if _, seen := varFirst[id]; !seen {
					varFirst[id] = s
				}
			}
			for _, w := range r.Withdrawn {
				if _, seen := retired[w.ID]; !seen {
					retired[w.ID] = w
				}
			}
		}
		for id, s := range baseFirst {
			c := at(s.Symbol, string(s.Timeframe))
			c.BaselinePublished++
			if _, ok := varFirst[id]; ok {
				c.Published++
				continue
			}
			c.Refused++
			switch {
			case func() bool { _, broke := brokenAt(baseCapture[id].Data, s, v.Buffer); return broke }():
				c.RefusedByReason["ob_closed_through"]++
			case v.Touches > 0 && int(s.Zone) > v.Touches:
				c.RefusedByReason["touches"]++
			default:
				c.RefusedByReason["other_selection"]++
			}
		}
		for id, s := range varFirst {
			if _, ok := baseFirst[id]; !ok {
				at(s.Symbol, string(s.Timeframe)).NewlyReachable++
			}
		}
		for id, w := range retired {
			s, ok := varFirst[id]
			if !ok {
				continue
			}
			at(s.Symbol, string(w.Timeframe)).RetiredByRuleC++
		}
		for _, byTF := range vr.Cells {
			for _, c := range byTF {
				vr.Totals.Published += c.Published
				vr.Totals.BaselinePublished += c.BaselinePublished
				vr.Totals.Refused += c.Refused
				vr.Totals.RetiredByRuleC += c.RetiredByRuleC
				vr.Totals.NewlyReachable += c.NewlyReachable
			}
		}
		vr.Totals.RefusedByReason = map[string]int{}
		for _, byTF := range vr.Cells {
			for _, c := range byTF {
				for reason, n := range c.RefusedByReason {
					vr.Totals.RefusedByReason[reason] += n
				}
			}
		}
		// The reference trades: live at the fill = published by then and not withdrawn by then.
		vr.LosersFixed, vr.WinnerKept = true, true
		for _, ref := range references {
			f := fate{Reference: ref.Name + " " + ref.Result}
			var live bool
			var first int64
			var withdrawnAt int64
			for pi, p := range paths {
				if !strings.Contains(p, strings.ToLower(ref.Symbol)) || !strings.Contains(p, "obwindow") {
					continue
				}
				r := results[vi][pi]
				for id, s := range r.First {
					if s.StructuralID != ref.StructuralID || s.Bar > ref.FilledAt {
						continue
					}
					if first == 0 || s.Bar < first {
						first = s.Bar
					}
					gone := false
					for _, w := range r.Withdrawn {
						if w.ID == id && w.At <= ref.FilledAt {
							gone = true
							if withdrawnAt == 0 || w.At < withdrawnAt {
								withdrawnAt = w.At
							}
						}
					}
					if !gone {
						live = true
					}
				}
			}
			switch {
			case first == 0:
				f.Fate = "never published before the fill"
			case live:
				f.Fate = "published " + barLabel(first) + " and still live at the fill " + barLabel(ref.FilledAt)
			default:
				f.Fate = "published " + barLabel(first) + ", withdrawn " + barLabel(withdrawnAt) + " before the fill " + barLabel(ref.FilledAt)
			}
			f.LiveAtFill = live
			f.Pass = live == ref.MustPublish
			if ref.MustPublish && !f.Pass {
				vr.WinnerKept = false
			}
			if !ref.MustPublish && !f.Pass {
				vr.LosersFixed = false
			}
			vr.References = append(vr.References, f)
		}
		vr.PassesAll = vr.LosersFixed && vr.WinnerKept
		out = append(out, vr)
	}

	// Candidate characteristics the definition does NOT gate (reported only): the confirmation type and the entry distance of the frozen behaviour's candidates.
	confirmations := map[string]map[string]int{}
	var distances []float64
	seen := map[string]bool{}
	for pi := range paths {
		for id, s := range base[pi].First {
			if seen[id] {
				continue
			}
			seen[id] = true
			g := "FX"
			if s.Symbol == "XAU" {
				g = "XAU"
			}
			if confirmations[g] == nil {
				confirmations[g] = map[string]int{}
			}
			confirmations[g][s.Confirmation]++
			distances = append(distances, s.EntryDistATR)
		}
	}
	sort.Float64s(distances)
	pick := func(p float64) float64 {
		if len(distances) == 0 {
			return 0
		}
		return float64(int(distances[int(p*float64(len(distances)-1))]*100+0.5)) / 100
	}
	names := make([]string, len(paths))
	for i, p := range paths {
		names[i] = filepath.Base(p)
	}
	raw, err := json.MarshalIndent(map[string]any{
		"captures":   names,
		"unit":       "one confirmed order_block opportunity per id (first sight across overlapping captures), engine as configured except the order block definition, which each variant sets; baseline = buffer 0 / touches off (order_block v5)",
		"reasons":    "ob_closed_through is re-derived from the capture's own bars independently of the engine; touches = zone_touches above the limit; other_selection = absent for a reason the two rules do not explain (the single-best selection moved)",
		"references": references,
		"variants":   out,
		"unchanged_by_the_definition": map[string]any{
			"other_strategies_identical_in_every_variant": !t.Failed(),
			"confirmation_type_of_frozen_candidates":      confirmations,
			"entry_distance_atr_of_frozen_candidates":     map[string]float64{"p50": pick(0.5), "p90": pick(0.9), "max": pick(1)},
		},
		"note": "A definition fix: the sample cannot validate expectancy.",
	}, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "order-block-definition-results.json"), append(raw, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, vr := range out {
		t.Logf("%-28s published %4d of %4d  refused %v  retired(C) %d  losers fixed %v  winner kept %v", vr.Name, vr.Totals.Published, vr.Totals.BaselinePublished, vr.Totals.RefusedByReason, vr.Totals.RetiredByRuleC, vr.LosersFixed, vr.WinnerKept)
	}
}
