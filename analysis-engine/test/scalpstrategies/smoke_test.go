// Package scalpstrategies_test replays the committed XAU captures through the real engine and checks what Round Number, Asia Range and Shock Retest
// publish on real bars: determinism, and that every published candidate keeps its own contract (a stop inside the instrument cap beyond the structure
// it protects, a target with room). It also reports, without asserting, how many candidates each strategy publishes and why real episodes were refused.
//
//	SCALP_SMOKE_OUT=/tmp/scalp.json go test ./test/scalpstrategies -run TestRealCaptureSmoke -v
package scalpstrategies_test

import (
	"encoding/json"
	"fmt"
	"math"
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/asiarange"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/roundnumber"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/shockretest"
)

var scalps = map[string]bool{"round_number": true, "asia_range": true, "shock_retest": true}

type captureResult struct {
	Capture    string
	Candidates []string       // sorted ids of the published candidates, for the determinism check
	Published  map[string]int // strategy|setup -> distinct candidates
	Refused    map[string]int // strategy|setup|reason -> distinct refused episodes
	Violations []string
	AsiaRanges int // distinct finalized Asia ranges the session read produced over the capture
	Shock      []string
}

func setupKind(c opportunity.Candidate) string {
	switch {
	case c.Strategy == "shock_retest":
		return "first_retest"
	case c.Reaction != nil && c.Reaction.Pattern == "sweep_reclaim":
		return "sweep_reclaim"
	default:
		return "break_retest"
	}
}

func replayOne(t *testing.T, doc *config.Document, name string, overrides map[string]map[string]any) captureResult {
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Fatal(err)
	}
	// The strategies themselves, to read their refusals: the same parameters the engine runs them with.
	var round *roundnumber.Strategy
	var asia *asiarange.Strategy
	var shock *shockretest.Strategy
	for _, cfg := range settings.Strategies {
		if extra := overrides[string(cfg.ID)]; extra != nil {
			merged := map[string]any{}
			for k, v := range cfg.Parameters {
				merged[k] = v
			}
			for k, v := range extra {
				merged[k] = v
			}
			cfg.Parameters = merged
		}
		var s strategy.Strategy
		switch cfg.ID {
		case "round_number":
			s, _ = roundnumber.New(cfg)
			round, _ = s.(*roundnumber.Strategy)
		case "asia_range":
			s, _ = asiarange.New(cfg)
			asia, _ = s.(*asiarange.Strategy)
		case "shock_retest":
			s, _ = shockretest.New(cfg)
			shock, _ = s.(*shockretest.Strategy)
		}
	}
	if round == nil || asia == nil || shock == nil {
		t.Fatal("the three scalp strategies must construct from the canonical configuration")
	}
	out := captureResult{Capture: name, Published: map[string]int{}, Refused: map[string]int{}}
	seen, refusedSeen := map[string]bool{}, map[string]bool{}
	asiaRanges := map[int64]bool{}
	pip := settings.Geometry.PipSize
	refuse := func(strategyID, kind, reason string, key string) {
		id := strategyID + "|" + kind + "|" + reason + "|" + key
		if !refusedSeen[id] {
			refusedSeen[id] = true
			out.Refused[strategyID+"|"+kind+"|"+reason]++
		}
	}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{StrategyParameters: overrides, OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 {
			return
		}
		if e.Context != nil {
			for _, level := range e.Context.Session.State.Levels {
				if level.Name == "ASIA_H" && level.Final {
					asiaRanges[level.AvailableAt] = true
				}
			}
		}
		for _, c := range e.Candidates {
			if !scalps[string(c.Strategy)] || seen[c.ID] {
				continue
			}
			seen[c.ID] = true
			out.Candidates = append(out.Candidates, c.ID)
			out.Published[string(c.Strategy)+"|"+setupKind(c)]++
			reference := float64(c.Entry.High)
			if c.Direction == market.Sell {
				reference = float64(c.Entry.Low)
			}
			risk := math.Abs(reference-float64(c.Invalidation.Price)) / pip
			reward := math.Abs(float64(c.Targets[0].Price.Price)-reference) / pip
			problem := func(format string, args ...any) {
				out.Violations = append(out.Violations, fmt.Sprintf("%s %s %s: %s", c.Strategy, c.Direction, c.ID, fmt.Sprintf(format, args...)))
			}
			if risk > settings.InstrumentStopMaxPips+0.11 {
				problem("stop %.1f pips is over the %.0f pip cap", risk, settings.InstrumentStopMaxPips)
			}
			beyond := float64(c.Invalidation.Price) < float64(c.Entry.Low)
			if c.Direction == market.Sell {
				beyond = float64(c.Invalidation.Price) > float64(c.Entry.High)
			}
			if !beyond {
				problem("invalidation %v is not beyond the entry band %v-%v", c.Invalidation.Price, c.Entry.Low, c.Entry.High)
			}
			if reward < risk-0.11 {
				problem("target %.1f pips is below 1R (%.1f pips)", reward, risk)
			}
			stopBudget := false
			budgetCode := ""
			for _, ev := range c.Evidence {
				if ev.Code == "stop_budget_structural" || ev.Code == "stop_budget_floor" {
					stopBudget, budgetCode = true, ev.Code
				}
			}
			if c.Strategy == "shock_retest" {
				// The room the executor really has: the budget widens a stop under the floor, and the first target must still offer 1R of that stop.
				widened := math.Max(risk, settings.InstrumentStopMinPips)
				out.Shock = append(out.Shock, fmt.Sprintf("%s %s risk %.0f pips reward %.0f pips (%s, %s) -> %.2fR of the %.0f pip executed stop", c.Direction, time.Unix(c.CreatedAt, 0).UTC().Format("01-02 15:04"), risk, reward, c.Targets[0].Price.Label, budgetCode, reward/widened, widened))
			}
			if !stopBudget {
				problem("no stop budget evidence code")
			}
		}
		for _, r := range []struct {
			id    string
			items func() []struct{ kind, reason, key string }
		}{
			{"round_number", func() []struct{ kind, reason, key string } {
				var out []struct{ kind, reason, key string }
				a, _ := round.Analyze(e.Context)
				for _, r := range a.Rejections {
					out = append(out, struct{ kind, reason, key string }{r.Kind, r.Reason, fmt.Sprintf("%v/%v/%d", r.Handle, r.Direction, r.At)})
				}
				return out
			}},
			{"asia_range", func() []struct{ kind, reason, key string } {
				var out []struct{ kind, reason, key string }
				a, _ := asia.Analyze(e.Context)
				for _, r := range a.Rejections {
					out = append(out, struct{ kind, reason, key string }{r.Kind, r.Reason, fmt.Sprintf("%v/%v/%d", r.Edge, r.Direction, r.At)})
				}
				return out
			}},
			{"shock_retest", func() []struct{ kind, reason, key string } {
				var out []struct{ kind, reason, key string }
				a, _ := shock.Analyze(e.Context)
				for _, r := range a.Rejections {
					out = append(out, struct{ kind, reason, key string }{"first_retest", r.Reason, fmt.Sprintf("%v/%d", r.Direction, r.At)})
				}
				return out
			}},
		} {
			for _, item := range r.items() {
				refuse(r.id, item.kind, item.reason, item.key)
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(out.Candidates)
	out.AsiaRanges = len(asiaRanges)
	return out
}

func TestRealCaptureSmoke(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	// The committed XAU captures (some without M1) and the fresh window that follows both 2026 clock changes (25 Oct London, 1 Nov New York).
	var matches []string
	for _, pattern := range []string{"replay-xau-production-capture-*.json", "replay-xau-m1-production-capture-*.json", "scalpwindow-xau-*.json"} {
		found, _ := filepath.Glob(filepath.Join("..", "..", "testdata", pattern))
		matches = append(matches, found...)
	}
	sort.Strings(matches)
	if len(matches) == 0 {
		t.Fatal("no committed XAU captures")
	}
	results := make([]captureResult, len(matches))
	again := make([]captureResult, len(matches))
	var wg sync.WaitGroup
	for i, m := range matches {
		wg.Add(1)
		go func(i int, name string) {
			defer wg.Done()
			results[i], again[i] = replayOne(t, doc, name, nil), replayOne(t, doc, name, nil)
		}(i, filepath.Base(m))
	}
	wg.Wait()
	totals := map[string]int{}
	refusals := map[string]int{}
	for i, r := range results {
		if strings.Join(r.Candidates, ",") != strings.Join(again[i].Candidates, ",") {
			t.Errorf("%s: a second replay published a different set of candidates", r.Capture)
		}
		for _, v := range r.Violations {
			t.Errorf("%s: %s", r.Capture, v)
		}
		for k, n := range r.Published {
			totals[k] += n
		}
		for k, n := range r.Refused {
			refusals[k] += n
		}
	}
	t.Logf("published per strategy and setup over %d XAU captures: %v", len(results), totals)

	// Side by side, per data set: what each strategy published and why real episodes were refused (not asserted).
	for _, r := range results {
		t.Logf("%s: %d Asia ranges finalized · published %v", r.Capture, r.AsiaRanges, r.Published)
		for _, line := range r.Shock {
			t.Logf("    shock_retest %s", line)
		}
		var keys []string
		for k := range r.Refused {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			t.Logf("    refused %-60s %d", k, r.Refused[k])
		}
	}
	if path := os.Getenv("SCALP_SMOKE_OUT"); path != "" {
		raw, _ := json.MarshalIndent(map[string]any{"captures": results, "published_total": totals, "refused_total": refusals}, "", " ")
		if err := os.WriteFile(path, append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}
