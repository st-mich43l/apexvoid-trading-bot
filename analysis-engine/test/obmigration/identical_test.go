// Package obmigration_test proves commit A of the order_block ownership change: the self-contained internal/strategy/orderblock package decides exactly what the master
// implementation (test/obmigration/reference, the strategy as it ran on master with the shared confirmation spine) decided. Both are evaluated on the
// SAME engine context at every closed M5 bar of every committed capture; their confirmed candidates must be deeply equal.
//
// Removed in the rebuild commit (order_block v7), which changes behaviour on purpose.
package obmigration_test

import (
	"fmt"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/orderblock"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/obmigration/reference"
)

func captures(t testing.TB) []string {
	t.Helper()
	var out []string
	for _, pattern := range []string{"replay-*-production-capture-*.json", "obwindow-*.json", "fvgwindow-*.json"} {
		matches, _ := filepath.Glob(filepath.Join("..", "..", "testdata", pattern))
		for _, m := range matches {
			if !strings.Contains(m, "-m1-") {
				out = append(out, m)
			}
		}
	}
	sort.Strings(out)
	return out
}

type outcome struct {
	bars, confirmed int
	diffs           []string
}

func compareCapture(t testing.TB, doc *config.Document, path string) outcome {
	t.Helper()
	capture, err := replaycapture.Load(path)
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
	var cfg strategy.Config
	for _, c := range settings.Strategies {
		if c.ID == "order_block" {
			cfg = c
		}
	}
	old, err := reference.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	neu, err := orderblock.New(cfg)
	if err != nil {
		t.Fatal(err)
	}
	var out outcome
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		out.bars++
		var want []opportunity.Candidate
		for _, c := range old.Evaluate(e.Context) {
			if c.Reaction != nil { // the master's resting observations are deleted in this change (they never traded)
				want = append(want, c)
			}
		}
		got := neu.Evaluate(e.Context)
		out.confirmed += len(want)
		if !reflect.DeepEqual(want, got) && len(out.diffs) < 5 {
			out.diffs = append(out.diffs, fmt.Sprintf("bar %d: master %d candidate(s), package %d\n master: %+v\n package: %+v", e.BarTime, len(want), len(got), want, got))
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func TestSelfContainedOrderBlockDecidesExactlyLikeMasterOnEveryCapture(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	paths := captures(t)
	if len(paths) < 21 {
		t.Fatalf("expected the committed captures, found %d", len(paths))
	}
	results := make([]outcome, len(paths))
	var wg sync.WaitGroup
	gate := make(chan struct{}, runtime.NumCPU())
	for i := range paths {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			gate <- struct{}{}
			defer func() { <-gate }()
			results[i] = compareCapture(t, doc, paths[i])
		}(i)
	}
	wg.Wait()
	bars, confirmed := 0, 0
	for i, r := range results {
		bars += r.bars
		confirmed += r.confirmed
		for _, d := range r.diffs {
			t.Errorf("%s: %s", filepath.Base(paths[i]), d)
		}
	}
	t.Logf("%d captures, %d evaluated M5 bars, %d confirmed master candidates, all identical", len(paths), bars, confirmed)
	if confirmed == 0 {
		t.Fatal("the replay produced no confirmed order_block candidate: the test proves nothing")
	}
}
