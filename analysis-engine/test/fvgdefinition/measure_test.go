package fvgdefinition_test

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

type quantiles struct {
	P10    float64 `json:"p10"`
	Median float64 `json:"median"`
	P90    float64 `json:"p90"`
}

func quantilesOf(values []float64) quantiles {
	if len(values) == 0 {
		return quantiles{}
	}
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	at := func(p float64) float64 {
		position := p * float64(len(sorted)-1)
		lo := int(math.Floor(position))
		hi := int(math.Ceil(position))
		return round(sorted[lo] + (sorted[hi]-sorted[lo])*(position-float64(lo)))
	}
	return quantiles{at(0.1), at(0.5), at(0.9)}
}

func round(v float64) float64 { return math.Round(v*100) / 100 }

// cell is one symbol class (XAU or FX) on one structure timeframe. Unit: one confirmed fvg candidate per opportunity id (first sight across overlapping captures).
type cell struct {
	Candidates          int            `json:"candidates"`
	DistinctGaps        int            `json:"distinct_gaps"`
	Refused             int            `json:"refused"`
	RefusedByReason     map[string]int `json:"refused_by_reason"`
	RefusedDistinctGaps int            `json:"refused_distinct_gaps"`
	Published           int            `json:"published"`
	ShareRefused        float64        `json:"share_refused"`
	GapATR              quantiles      `json:"gap_width_in_own_atr"`
	MiddleBodyATR       quantiles      `json:"middle_body_in_own_atr"`
	NewlyReachable      int            `json:"newly_reachable"`

	gapATR, bodyATR   []float64
	gaps, refusedGaps map[string]bool
}

func newCell() *cell {
	return &cell{RefusedByReason: map[string]int{}, gaps: map[string]bool{}, refusedGaps: map[string]bool{}}
}

// opportunity is one fvg opportunity id: whether v5 (the definition removed) and v6 publish it, and its gap judged from the capture's own bars.
type opportunity struct {
	sighting    sighting
	capture     *replaycapture.Capture
	class       string
	reason      string
	inOff, inOn bool
}

// TestMeasureFVGDefinition replays every committed M5 capture twice with the engine as configured: with the definition removed (what fvg v5 published) and
// with it, and writes how many gaps no longer trade, by symbol class and structure timeframe.
//
//	FVG_DEFINITION_OUT=/tmp/fvg go test ./test/fvgdefinition -run TestMeasureFVGDefinition -timeout 60m
func TestMeasureFVGDefinition(t *testing.T) {
	dir := os.Getenv("FVG_DEFINITION_OUT")
	if dir == "" {
		t.Skip("FVG_DEFINITION_OUT not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc := loadDoc(t)
	th, atrLength := configuredThresholds(t, doc), atrLengthOf(t, doc)
	names := captureNames(t)

	type result struct {
		off, on             map[key]sighting
		othersOff, othersOn map[string]string
		capture             *replaycapture.Capture
	}
	results := map[string]result{}
	var mu sync.Mutex
	var wg sync.WaitGroup
	for _, name := range names {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			off, othersOff, capture := replayFVGAndOthers(t, doc, name, true)
			on, othersOn, _ := replayFVGAndOthers(t, doc, name, false)
			mu.Lock()
			results[name] = result{off, on, othersOff, othersOn, capture}
			mu.Unlock()
		}(name)
	}
	wg.Wait()

	classOf := func(name string) string {
		if strings.Contains(name, "-xau-") {
			return "XAU"
		}
		return "FX"
	}
	cells := map[string]*cell{}
	at := func(class string, tf market.Timeframe) *cell {
		k := class + " " + string(tf)
		if cells[k] == nil {
			cells[k] = newCell()
		}
		return cells[k]
	}

	// Per evaluated bar: what v5 published and the definition removes must be a gap the definition refuses (judged here from the capture's bars, not by the
	// engine); what the definition publishes that v5 did not is reachable only because a refused gap no longer wins the single-best selection; a candidate
	// both publish is published unchanged.
	opportunities := map[string]*opportunity{}
	var order []string
	judge := func(name string, s sighting, r result) *opportunity {
		op := opportunities[s.ID]
		if op == nil {
			m, ok := measureGap(t, r.capture, atrLength, s)
			if !ok {
				t.Errorf("%s %s: cannot find the completing %s bar %s in the capture", name, s.ID, s.Timeframe, barLabel(s.FormedAt))
				return nil
			}
			op = &opportunity{sighting: s, capture: r.capture, class: classOf(name), reason: m.reason(th)}
			opportunities[s.ID] = op
			order = append(order, s.ID)
		}
		return op
	}
	reachableSightings, droppedWithoutRefusal, changed, othersChecked := 0, 0, 0, 0
	for _, name := range names {
		r := results[name]
		// The definition touches the fair value gap strategy only: every other strategy publishes exactly the same candidates.
		otherDiffs := 0
		for k, v := range r.othersOff {
			if r.othersOn[k] != v {
				otherDiffs++
			}
		}
		for k := range r.othersOn {
			if _, ok := r.othersOff[k]; !ok {
				otherDiffs++
			}
		}
		if otherDiffs != 0 {
			t.Errorf("%s: %d candidates of strategies other than fvg changed with the definition", name, otherDiffs)
		}
		othersChecked += len(r.othersOff)
		for k, s := range r.off {
			op := judge(name, s, r)
			if op == nil {
				continue
			}
			op.inOff = true
			m, _ := measureGap(t, r.capture, atrLength, s)
			reason := m.reason(th)
			o, published := r.on[k]
			switch {
			case published && reason != "":
				t.Errorf("%s %s %s: published although its gap is %q (%.2f ATR, body %.2f ATR)", name, barLabel(k.bar), s.ID, reason, m.GapATR, m.BodyATR)
			case published && (o.EntryLow != s.EntryLow || o.EntryHigh != s.EntryHigh || o.Invalidation != s.Invalidation || o.Target != s.Target):
				changed++
				t.Errorf("%s %s %s: the definition moved a gap it publishes", name, barLabel(k.bar), s.ID)
			case !published && reason == "":
				droppedWithoutRefusal++
				t.Errorf("%s %s %s: v5 published a real gap (%.2f ATR) that the definition dropped", name, barLabel(k.bar), s.ID, m.GapATR)
			}
			if published {
				op.inOn = true
			}
		}
		for k, s := range r.on {
			if _, was := r.off[k]; was {
				continue
			}
			reachableSightings++
			op := judge(name, s, r)
			if op == nil {
				continue
			}
			if op.reason != "" {
				t.Errorf("%s %s %s: published with the definition but the gap is %q", name, barLabel(k.bar), s.ID, op.reason)
			}
			op.inOn = true
		}
	}

	for _, id := range order {
		op := opportunities[id]
		c := at(op.class, op.sighting.Timeframe)
		if !op.inOff {
			c.NewlyReachable++
			continue
		}
		m, _ := measureGap(t, op.capture, atrLength, op.sighting)
		c.Candidates++
		c.gaps[op.sighting.StructuralID] = true
		c.gapATR = append(c.gapATR, m.GapATR)
		c.bodyATR = append(c.bodyATR, m.BodyATR)
		if op.reason != "" {
			c.Refused++
			c.RefusedByReason[op.reason]++
			c.refusedGaps[op.sighting.StructuralID] = true
		}
		if op.inOn {
			c.Published++
		}
	}

	keys := make([]string, 0, len(cells))
	for k := range cells {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	total := newCell()
	for _, k := range keys {
		c := cells[k]
		c.DistinctGaps, c.RefusedDistinctGaps = len(c.gaps), len(c.refusedGaps)
		if c.Candidates > 0 {
			c.ShareRefused = round(float64(c.Refused) / float64(c.Candidates))
		}
		c.GapATR, c.MiddleBodyATR = quantilesOf(c.gapATR), quantilesOf(c.bodyATR)
		total.Candidates += c.Candidates
		total.Refused += c.Refused
		total.Published += c.Published
		total.NewlyReachable += c.NewlyReachable
		total.DistinctGaps += c.DistinctGaps
		total.RefusedDistinctGaps += c.RefusedDistinctGaps
		for reason, n := range c.RefusedByReason {
			total.RefusedByReason[reason] += n
		}
	}
	if total.Candidates > 0 {
		total.ShareRefused = round(float64(total.Refused) / float64(total.Candidates))
	}
	if droppedWithoutRefusal != 0 || changed != 0 {
		t.Errorf("%d real gaps dropped, %d published gaps moved", droppedWithoutRefusal, changed)
	}

	raw, err := json.MarshalIndent(map[string]any{
		"captures": names,
		"definition": map[string]any{
			"minimum_gap_atr": th.gapATR, "minimum_displacement_body_atr": th.bodyATR, "minimum_displacement_body_ratio": th.bodyRatio,
			"judged_on": "the bars and ATR of the timeframe the gap was found on, as of the candle that completed it",
			"reasons":   []string{techniquezone.FVGRefusedGapSmall, techniquezone.FVGRefusedDisplacementWeak, techniquezone.FVGRefusedDisplacementDirection},
		},
		"unit":                                        "one confirmed fvg candidate per opportunity id (first sight across overlapping captures), engine as configured (stop budget on); distinct_gaps counts the structural gaps behind them",
		"by_class_and_timeframe":                      cells,
		"total":                                       total,
		"newly_reachable_opportunities":               total.NewlyReachable,
		"newly_reachable_sightings":                   reachableSightings,
		"other_strategies_candidates_checked":         othersChecked,
		"other_strategies_identical":                  true,
		"published_set_unchanged_apart_from_refusals": droppedWithoutRefusal == 0 && changed == 0,
		"note": "refused = a candidate fvg v5 published whose gap the definition refuses (reason judged here from the capture's own bars, in the engine's order); published = still published; newly_reachable = published only with the definition, because a refused gap no longer wins the single-best selection, counted apart and not in candidates; the quantiles are over every v5 candidate of the cell",
	}, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "fvg-definition-results.json"), append(raw, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Logf("%d fvg candidates across %d captures: %d refused (%.0f%%), %d newly reachable", total.Candidates, len(names), total.Refused, 100*total.ShareRefused, total.NewlyReachable)
}
