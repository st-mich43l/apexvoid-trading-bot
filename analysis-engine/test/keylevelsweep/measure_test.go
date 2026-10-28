package keylevelsweep_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

type sighting struct {
	Bar          int64   `json:"bar"`
	ConfirmTime  int64   `json:"confirm_time"`
	ID           string  `json:"id"`
	Direction    string  `json:"direction"`
	EntryLow     float64 `json:"entry_low"`
	EntryHigh    float64 `json:"entry_high"`
	Invalidation float64 `json:"invalidation"`
	Swept        bool    `json:"swept"` // the invalidation is anchored to the reaction's own extreme
	RuleEvidence bool    `json:"-"`
}

type key struct {
	capture string
	bar     int64
	id      string
}

// replayAll replays one capture and returns every Key Level candidate it published, per evaluated bar.
func replayAll(t *testing.T, doc *config.Document, name string, withoutRule bool) map[key]sighting {
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Error(err)
		return nil
	}
	out := map[key]sighting{}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{WithoutKeyLevelSweep: withoutRule, OnEvaluation: func(e engine.Evaluation) {
		for _, c := range e.Candidates {
			if string(c.Strategy) != "key_level" || c.Reaction == nil {
				continue
			}
			out[key{name, e.BarTime, c.ID}] = sighting{
				Bar: e.BarTime, ConfirmTime: c.Reaction.ConfirmationBarTime, ID: c.ID, Direction: string(c.Direction),
				EntryLow: float64(c.Entry.Low), EntryHigh: float64(c.Entry.High), Invalidation: float64(c.Invalidation.Price),
				Swept: hasCode(c, strategyutil.ReactionExtremeEvidence), RuleEvidence: hasCode(c, keylevel.ReactionSweptEvidence),
			}
		}
	}})
	if err != nil {
		t.Error(err)
	}
	return out
}

func periodOf(confirm int64) string {
	at := time.Unix(confirm, 0).UTC()
	switch {
	case at.Before(time.Date(2026, 9, 30, 0, 0, 0, 0, time.UTC)):
		return "before_2026-09-30"
	case at.Before(time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC)):
		return "2026-09-30"
	case at.Before(time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC)):
		return "2026-10-01_to_10-11"
	default:
		return "from_2026-10-12"
	}
}

type cell struct {
	Total             int `json:"total"`
	Swept             int `json:"swept"`
	Unswept           int `json:"unswept"`
	Refused           int `json:"refused"`
	PublishedWithRule int `json:"published_with_rule"`
	RecoveredBySelect int `json:"recovered_by_per_attempt_selection"`
}

// TestMeasureKeyLevelSweep replays every committed M5 capture twice with the engine as configured: with the XAU sweep rule removed (what v5 published) and
// with it. It asserts the rule does exactly what it says and writes the counts by period.
//
//	KEYLEVEL_SWEEP_OUT=/tmp/kls go test ./test/keylevelsweep -run TestMeasureKeyLevelSweep -timeout 60m
func TestMeasureKeyLevelSweep(t *testing.T) {
	dir := os.Getenv("KEYLEVEL_SWEEP_OUT")
	if dir == "" {
		t.Skip("KEYLEVEL_SWEEP_OUT not set")
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
	var wg sync.WaitGroup
	type result struct{ off, on map[key]sighting }
	results := map[string]result{}
	for _, name := range names {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			off, on := replayAll(t, doc, name, true), replayAll(t, doc, name, false)
			mu.Lock()
			results[name] = result{off, on}
			mu.Unlock()
		}(name)
	}
	wg.Wait()

	// Invariants, per capture and evaluated bar.
	cells := map[string]*cell{}
	at := func(p string) *cell {
		if cells[p] == nil {
			cells[p] = &cell{}
		}
		return cells[p]
	}
	type opportunity struct {
		off, on   sighting
		published bool
	}
	first := map[string]*opportunity{}
	var order []string
	fxIdentical, fxCandidates := true, 0
	totalRecovered := 0
	for _, name := range names {
		r := results[name]
		isXAU := strings.Contains(name, "-xau-")
		if !isXAU {
			// The rule is XAU-only: on every other instrument the two replays must publish exactly the same Key Level candidates.
			fxCandidates += len(r.off)
			if len(r.off) != len(r.on) {
				fxIdentical = false
				t.Errorf("%s: %d Key Level candidates without the rule, %d with it; the rule is XAU-only", name, len(r.off), len(r.on))
			}
			for k, s := range r.off {
				if o, ok := r.on[k]; !ok || o.EntryLow != s.EntryLow || o.EntryHigh != s.EntryHigh || o.Invalidation != s.Invalidation {
					fxIdentical = false
					t.Errorf("%s %s: changed by an XAU-only rule", name, k.id)
				}
			}
			continue
		}
		byBar := map[int64][]key{}
		for k := range r.off {
			byBar[k.bar] = append(byBar[k.bar], k)
		}
		for k, s := range r.on {
			if !s.Swept || !s.RuleEvidence {
				t.Errorf("%s %s: published under the rule without having swept the level (swept=%v evidence=%v)", name, k.id, s.Swept, s.RuleEvidence)
			}
			byBar[k.bar] = byBar[k.bar]
		}
		// 1. a swept candidate v5 published is published, unchanged; 2. an unswept one is not; 3. what only the rule publishes is recovered.
		for k, s := range r.off {
			o, published := r.on[k]
			switch {
			case s.Swept && !published:
				t.Errorf("%s %s: v5 published a swept candidate that the rule dropped", name, k.id)
			case s.Swept && (o.EntryLow != s.EntryLow || o.EntryHigh != s.EntryHigh || o.Invalidation != s.Invalidation):
				t.Errorf("%s %s: the rule moved a swept candidate", name, k.id)
			case !s.Swept && published:
				t.Errorf("%s %s: an unswept candidate was published under the rule", name, k.id)
			}
		}
		for k := range r.on {
			if _, was := r.off[k]; !was {
				// published only with the rule: another level's swept attempt, which an unswept higher-stars attempt had displaced
				totalRecovered++
				for _, other := range byBar[k.bar] {
					if r.off[other].Swept {
						t.Errorf("%s %s: recovered at a bar where v5 already published a swept candidate", name, k.id)
					}
				}
			}
		}
		// opportunity-level counts (first sight by id; overlapping captures replay the same period)
		for k, s := range r.off {
			op, seen := first[k.id]
			if !seen {
				op = &opportunity{off: s}
				first[k.id] = op
				order = append(order, k.id)
			}
			if _, published := r.on[k]; published {
				op.published = true
			}
		}
		for k, s := range r.on {
			if _, was := r.off[k]; was {
				continue
			}
			op, seen := first[k.id]
			if !seen {
				op = &opportunity{off: sighting{}, on: s}
				first[k.id] = op
				order = append(order, k.id)
				at(periodOf(s.ConfirmTime)).RecoveredBySelect++
			}
			op.published = true
		}
	}
	for _, id := range order {
		op := first[id]
		if op.off.ID == "" {
			continue // recovered only
		}
		c := at(periodOf(op.off.ConfirmTime))
		c.Total++
		if op.off.Swept {
			c.Swept++
		} else {
			c.Unswept++
		}
		if op.published {
			c.PublishedWithRule++
		} else {
			c.Refused++
		}
	}
	for p, c := range cells {
		if c.Refused != c.Unswept && p != "" {
			// An opportunity id seen unswept first and swept later is counted by its first sight; the refusal is the sighting, so allow a small drift.
			t.Logf("%s: refused %d, unswept %d", p, c.Refused, c.Unswept)
		}
	}
	periods := make([]string, 0, len(cells))
	for p := range cells {
		periods = append(periods, p)
	}
	sort.Strings(periods)
	total := cell{}
	for _, p := range periods {
		c := cells[p]
		total.Total += c.Total
		total.Swept += c.Swept
		total.Unswept += c.Unswept
		total.Refused += c.Refused
		total.PublishedWithRule += c.PublishedWithRule
		total.RecoveredBySelect += c.RecoveredBySelect
	}
	raw, err := json.MarshalIndent(map[string]any{
		"captures":                names,
		"rule":                    "XAU Key Level publishes a confirmed reaction only when it swept the level (require_reaction_sweep)",
		"unit":                    "one XAU Key Level confirmed candidate per opportunity id (first sight across overlapping captures), engine as configured (stop budget on)",
		"by_period":               cells,
		"total":                   total,
		"non_xau_candidates":      fxCandidates,
		"non_xau_identical":       fxIdentical,
		"recovered_opportunities": total.RecoveredBySelect,
		"recovered_sightings":     totalRecovered,
		"published_with_rule_including_recovered": total.PublishedWithRule + total.RecoveredBySelect,
		"note": "refused = an opportunity v5 published (first sight by id) that the rule does not publish; recovered = an opportunity published only with the rule (another level's swept attempt that an unswept higher-stars attempt had displaced), counted separately and not in total/swept/unswept/refused; recovered_sightings counts it once per evaluated bar and capture",
	}, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "keylevel-sweep-first-results.json"), append(raw, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Logf("%d XAU Key Level opportunities across %d captures: %d swept, %d unswept, %d refused, %d recovered", total.Total, len(names), total.Swept, total.Unswept, total.Refused, totalRecovered)
}
