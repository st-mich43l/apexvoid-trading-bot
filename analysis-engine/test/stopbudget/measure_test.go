// Package stopbudget_test measures the instrument stop budget the legacy-family strategies now apply (key_level, supply, demand, order_block, fvg,
// flip_zone, trendline): a structural stop wider than the instrument cap is refused, one tighter than the floor is published and widened by execution,
// one inside is published as it is.
//
//	STOP_BUDGET_OUT=/tmp/sb go test ./test/stopbudget -run TestMeasureStopBudget -timeout 40m
//
// Every committed M5 capture is replayed twice: without the budget (what the strategies published before this change, the reaction-anchored stop included)
// and with it (the engine as configured). The first run classifies every confirmed candidate against the instrument envelope; the second must publish exactly
// the candidates that are not refused, with the same entry and invalidation and the matching evidence code. Nothing here is an outcome and nothing is tuned.
package stopbudget_test

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

var legacyFamily = map[string]bool{"key_level": true, "supply": true, "demand": true, "order_block": true, "fvg": true, "flip_zone": true, "trendline": true}

const (
	structuralCode = "stop_budget_structural"
	floorCode      = "stop_budget_floor"
)

type record struct {
	ID, Strategy, Symbol, Direction, Capture string
	ConfirmTime, BarTime                     int64
	EntryLow, EntryHigh, Reference           float64
	Invalidation, Target, ATR, Pip           float64
	Price                                    float64 // last close of the evaluated M5 bar: the price the plan is built at
	RiskPips, MinPips, MaxPips               float64
	TargetPips                               float64
	Class                                    string // refused | structural | floor
	Pattern                                  string
	Evidence                                 []string
	// RefusedFirstSightPublishedLater: the first evaluation of this opportunity was over the cap, a later one (new ATR) was inside the budget.
	RefusedFirstSightPublishedLater bool
}

func evidenceCodes(c opportunity.Candidate) []string {
	var out []string
	for _, e := range c.Evidence {
		out = append(out, e.Code)
	}
	return out
}

func has(codes []string, want string) bool {
	for _, c := range codes {
		if c == want {
			return true
		}
	}
	return false
}

func classify(risk, min, max float64) string {
	switch {
	case max > 0 && risk > max+1e-9:
		return "refused"
	case min > 0 && risk < min:
		return "floor"
	default:
		return "structural"
	}
}

// replayOne replays one capture with or without the stop budget. The XAU Key Level sweep rule (docs/strategies/keylevel-sweep-first.md) is always removed
// here: this measurement isolates the budget, and the rule refuses setups before the budget could classify them.
func replayOne(t *testing.T, doc *config.Document, name string, withBudget bool) []record {
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Error(err)
		return nil
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Error(err)
		return nil
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Error(err)
		return nil
	}
	var out []record
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{WithoutStopBudget: !withBudget, WithoutKeyLevelSweep: true, OnEvaluation: func(e engine.Evaluation) {
		for _, c := range e.Candidates {
			strategy := string(c.Strategy)
			if !legacyFamily[strategy] || c.Reaction == nil {
				continue
			}
			buy := c.Direction == market.Buy
			reference := c.Entry.High
			if !buy {
				reference = c.Entry.Low
			}
			pip := settings.Geometry.PipSize
			atr, price := 0.0, 0.0
			if frame := e.Context.Timeframes[market.M5]; frame != nil && frame.Legacy != nil {
				atr = frame.Legacy.DetectorATR
				if n := len(frame.Legacy.Bars); n > 0 {
					price = frame.Legacy.Bars[n-1].Close
				}
			}
			risk := math.Abs(float64(reference)-float64(c.Invalidation.Price)) / pip
			r := record{ID: c.ID, Strategy: strategy, Symbol: string(c.Symbol), Direction: string(c.Direction), Capture: name, ConfirmTime: c.Reaction.ConfirmationBarTime, BarTime: e.BarTime,
				EntryLow: float64(c.Entry.Low), EntryHigh: float64(c.Entry.High), Reference: float64(reference), Invalidation: float64(c.Invalidation.Price),
				ATR: atr, Price: price, Pip: pip, RiskPips: risk, MinPips: settings.InstrumentStopMinPips, MaxPips: settings.InstrumentStopMaxPips, Pattern: c.Reaction.Pattern, Evidence: evidenceCodes(c)}
			if len(c.Targets) > 0 {
				r.Target = float64(c.Targets[0].Price.Price)
				r.TargetPips = math.Abs(r.Target-float64(reference)) / pip
			}
			r.Class = classify(risk, r.MinPips, r.MaxPips)
			out = append(out, r)
		}
	}})
	if err != nil {
		t.Error(err)
	}
	return out
}

func TestMeasureStopBudget(t *testing.T) {
	dir := os.Getenv("STOP_BUDGET_OUT")
	if dir == "" {
		t.Skip("STOP_BUDGET_OUT not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	matches, _ := filepath.Glob(filepath.Join("..", "..", "testdata", "replay-*-production-capture-*.json"))
	for _, m := range matches {
		if !strings.Contains(m, "-m1-") {
			names = append(names, filepath.Base(m))
		}
	}
	sort.Strings(names)
	var mu sync.Mutex
	var all []record
	var wg sync.WaitGroup
	for _, name := range names {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			before := replayOne(t, doc, name, false)
			after := replayOne(t, doc, name, true)
			type occurrence struct {
				bar int64
				id  string
			}
			afterAt := map[occurrence]record{}
			for _, r := range after {
				afterAt[occurrence{r.BarTime, r.ID}] = r
			}
			for _, b := range before {
				a, published := afterAt[occurrence{b.BarTime, b.ID}]
				switch b.Class {
				case "refused":
					if published && b.RiskPips > b.MaxPips+1.5*b.Pip/10/b.Pip {
						t.Errorf("%s %s %s: %.1f pips is above the cap %.0f yet the budgeted engine published it", name, b.Strategy, b.ID, b.RiskPips, b.MaxPips)
					}
				default:
					if !published {
						t.Errorf("%s %s %s: %.2f pips is inside the budget (%s) yet the budgeted engine did not publish it", name, b.Strategy, b.ID, b.RiskPips, b.Class)
						continue
					}
					// The engine classifies the unrounded stop; the record only has the tick-rounded published price, so a risk within one tick of the
					// floor may read either way.
					tick := 1.5 * b.Pip / 10 / b.Pip // one rounding tick plus float noise
					okStructural := b.MinPips <= 0 || b.RiskPips >= b.MinPips-tick
					okFloor := b.MinPips > 0 && b.RiskPips <= b.MinPips+tick
					gotStructural, gotFloor := has(a.Evidence, structuralCode), has(a.Evidence, floorCode)
					if gotStructural == gotFloor || (gotStructural && !okStructural) || (gotFloor && !okFloor) {
						t.Errorf("%s %s %s: %.2f pips (floor %.0f): evidence %v, want exactly one of %s/%s consistent with the risk", name, b.Strategy, b.ID, b.RiskPips, b.MinPips, a.Evidence, structuralCode, floorCode)
					}
					if a.Invalidation != b.Invalidation || a.EntryLow != b.EntryLow || a.EntryHigh != b.EntryHigh {
						t.Errorf("%s %s %s: the budget moved the entry or the invalidation", name, b.Strategy, b.ID)
					}
				}
			}
			beforeAt := map[occurrence]bool{}
			for _, b := range before {
				beforeAt[occurrence{b.BarTime, b.ID}] = true
			}
			for _, a := range after {
				if !beforeAt[occurrence{a.BarTime, a.ID}] {
					t.Errorf("%s %s %s: published only with the budget", name, a.Strategy, a.ID)
				}
			}
			mu.Lock()
			all = append(all, before...)
			mu.Unlock()
		}(name)
	}
	wg.Wait()
	sort.SliceStable(all, func(i, j int) bool {
		if all[i].Capture != all[j].Capture {
			return all[i].Capture < all[j].Capture
		}
		if all[i].BarTime != all[j].BarTime {
			return all[i].BarTime < all[j].BarTime
		}
		return all[i].ID < all[j].ID
	})
	all = oncePerOpportunity(all)
	write := func(name string, v any) {
		raw, err := json.MarshalIndent(v, "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, name), append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	out, err := os.Create(filepath.Join(dir, "records.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	enc := json.NewEncoder(out)
	for _, r := range all {
		if err := enc.Encode(r); err != nil {
			t.Fatal(err)
		}
	}
	out.Close()
	write("results.json", aggregate(all))
	t.Logf("%d confirmed legacy-family candidates across %d captures", len(all), len(names))
}

func quantile(v []float64, q float64) float64 {
	if len(v) == 0 {
		return 0
	}
	s := append([]float64(nil), v...)
	sort.Float64s(s)
	return s[int(math.Round(q*float64(len(s)-1)))]
}

func round(v float64) float64 { return math.Round(v*100) / 100 }

type cell struct {
	N              int     `json:"n"`
	Refused        int     `json:"refused_over_cap"`
	RefusedShare   float64 `json:"refused_share"`
	Structural     int     `json:"structural_in_budget"`
	StructuralPct  float64 `json:"structural_share"`
	Floor          int     `json:"floor_below_min"`
	FloorShare     float64 `json:"floor_share"`
	LaterWithin    int     `json:"refused_at_first_sight_published_later"`
	MedianRefused  float64 `json:"median_risk_pips_refused"`
	MedianInBudget float64 `json:"median_risk_pips_structural"`
	MedianFloor    float64 `json:"median_risk_pips_floor"`
	MinPips        float64 `json:"envelope_min_pips"`
	MaxPips        float64 `json:"envelope_max_pips"`
}

func summarize(rs []record) cell {
	var c cell
	var ref, st, fl []float64
	for _, r := range rs {
		c.N++
		c.MinPips, c.MaxPips = r.MinPips, r.MaxPips
		if r.RefusedFirstSightPublishedLater {
			c.LaterWithin++
		}
		switch r.Class {
		case "refused":
			c.Refused++
			ref = append(ref, r.RiskPips)
		case "floor":
			c.Floor++
			fl = append(fl, r.RiskPips)
		default:
			c.Structural++
			st = append(st, r.RiskPips)
		}
	}
	if c.N > 0 {
		c.RefusedShare, c.StructuralPct, c.FloorShare = round(float64(c.Refused)/float64(c.N)), round(float64(c.Structural)/float64(c.N)), round(float64(c.Floor)/float64(c.N))
	}
	c.MedianRefused, c.MedianInBudget, c.MedianFloor = round(quantile(ref, .5)), round(quantile(st, .5)), round(quantile(fl, .5))
	return c
}

func aggregate(records []record) map[string]any {
	groups := map[string][]record{}
	for _, r := range records {
		groups[r.Strategy+"|"+r.Symbol] = append(groups[r.Strategy+"|"+r.Symbol], r)
		groups["all|"+r.Symbol] = append(groups["all|"+r.Symbol], r)
		groups[r.Strategy+"|all"] = append(groups[r.Strategy+"|all"], r)
		groups["all|all"] = append(groups["all|all"], r)
		if r.Symbol != "XAU" {
			groups["all|FX"] = append(groups["all|FX"], r)
			groups[r.Strategy+"|FX"] = append(groups[r.Strategy+"|FX"], r)
		}
	}
	cells := map[string]cell{}
	for k, rs := range groups {
		cells[k] = summarize(rs)
	}
	return map[string]any{
		"description":        "confirmed legacy-family candidates over the committed M5 captures, as the strategies published them before the stop budget (reaction-anchored stop included), classified against the instrument envelope; risk is measured from the proximal entry edge to the invalidation",
		"by_strategy_symbol": cells,
	}
}

// oncePerOpportunity keeps one record per opportunity id across all captures: the first evaluation that is inside the budget (the one the budgeted engine publishes first),
// or, when every evaluation is over the cap, the first one (refused). all must be ordered by capture and bar time.
func oncePerOpportunity(all []record) []record {
	// Overlapping captures replay the same period, so the id alone identifies an opportunity (the unit of the reaction-anchored-stop baseline).
	first := map[string]int{}
	var out []record
	for _, r := range all {
		k := r.ID
		i, seen := first[k]
		if !seen {
			first[k] = len(out)
			out = append(out, r)
			continue
		}
		if out[i].Class == "refused" && r.Class != "refused" {
			r.RefusedFirstSightPublishedLater = true
			out[i] = r
		}
	}
	return out
}
