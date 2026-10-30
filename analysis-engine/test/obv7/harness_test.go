// Package obv7_test holds the order_block v7 evidence: the vendored v6 (test/obv7/reference, the strategy as it ran on master) and the v7 package are evaluated on
// the SAME engine context at every closed M5 bar of the committed captures.
package obv7_test

import (
	"fmt"
	"path/filepath"
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/obv7/reference"
)

func resolve(t testing.TB) *config.Document {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

func testdata(name string) string { return filepath.Join("..", "..", "testdata", name) }

// captures are the committed M5 production captures (and the order block and fair value gap windows).
func captures(t testing.TB) []string {
	t.Helper()
	var out []string
	for _, pattern := range []string{"replay-*-production-capture-*.json", "obwindow-*.json", "fvgwindow-*.json"} {
		matches, _ := filepath.Glob(testdata(pattern))
		for _, m := range matches {
			if !strings.Contains(m, "-m1-") {
				out = append(out, m)
			}
		}
	}
	sort.Strings(out)
	return out
}

// masterParameters are the parameters strategies.order_block carried on master (v6), merged with what the engine injected for it: the shared detector
// thresholds (analysis.legacy_detector), the instrument scale, the shared confluence rubric and the fibonacci epsilon.
func masterParameters(settings engine.Settings) map[string]any {
	params := map[string]any{}
	for k, v := range settings.LegacyDetector {
		params[k] = v
	}
	for k, v := range map[string]any{
		"confirmed_break_buffer_atr": 0.1, "maximum_zone_touches": 2.0, "minimum_strength": 0.4, "minimum_zone_atr": 0.5, "invalidation_buffer_atr": 0.5,
		"minimum_target_distance_atr": 1.0, "expiry_hours": 24.0, "reaction_lookback_bars": 3.0, "engulfing_minimum_range_atr": 0.5, "epsilon_atr_fraction": 0.05,
		"maximum_zone_atr": 3.0, "entry_max_width_price": 5.0, "momentum_body_fraction": 0.6, "higher_timeframes": []any{"M15", "H1"},
		"pip_size": settings.LegacyRead.PipSize, "fvg_entry_max_width_price": settings.TechniqueZones.Technique.FVGEntryMaxWidthPrice,
		"confluence_scoring_version": settings.Confluence.ScoringVersion, "confluence_star_three_ratio": settings.Confluence.StarThreeRatio,
		"confluence_star_two_ratio": settings.Confluence.StarTwoRatio, "confluence_zone_quality_weight": settings.Confluence.ZoneQualityWeight,
		"confluence_mad_score_weight": settings.Confluence.MADScoreWeight, "fibonacci_confluence_weight": settings.Confluence.FibonacciWeight,
		"fibonacci_epsilon_atr": settings.Fib.EpsilonATR, "price_digits": float64(settings.Geometry.PriceDigits),
		"technique_retest_max_touches": float64(settings.TechniqueZones.Technique.RetestMaxTouches),
	} {
		params[k] = v
	}
	return params
}

// scaleOf are the parameters the master engine carried into the order block from the instrument: its pip size, entry-band width and stop envelope.
func scaleOf(v7 map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range v7 {
		switch k {
		case "pip_size", "execution_stop_max_pips", "execution_stop_min_pips", "execution_pip_size":
			out[k] = v
		case "entry_max_width_price":
			out["entry_max_width_price"] = v
			out["fvg_entry_max_width_price"] = v
		}
	}
	return out
}

// pairParameters are the instrument-scale parameters of the symbol, as the v7 strategy receives them.
func pairParameters(t testing.TB, doc *config.Document, symbol market.Symbol) map[string]any {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(symbol)); err != nil {
		t.Fatal(err)
	}
	for _, c := range settings.Strategies {
		if c.ID == "order_block" {
			return scaleOf(c.Parameters)
		}
	}
	return nil
}

type pair struct {
	v6 strategy.Strategy
	v7 *orderblock.Strategy
}

func newPair(t testing.TB, doc *config.Document, symbol market.Symbol) pair {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(symbol)); err != nil {
		t.Fatal(err)
	}
	var v7cfg strategy.Config
	for _, c := range settings.Strategies {
		if c.ID == "order_block" {
			v7cfg = c
		}
	}
	old := v7cfg
	old.Version = "v6"
	old.Parameters = masterParameters(settings)
	for k, v := range scaleOf(v7cfg.Parameters) {
		old.Parameters[k] = v
	}
	v6, err := reference.New(old)
	if err != nil {
		t.Fatalf("the vendored v6 cannot be built: %v", err)
	}
	v7, err := orderblock.New(v7cfg)
	if err != nil {
		t.Fatal(err)
	}
	return pair{v6: v6, v7: v7.(*orderblock.Strategy)}
}

// bar is one closed M5 bar's decisions of both versions.
type bar struct {
	time     int64
	v6, v7   []opportunity.Candidate
	refusals map[strategy.Refusal]int64
}

func confirmed(candidates []opportunity.Candidate) []opportunity.Candidate {
	var out []opportunity.Candidate
	for _, c := range candidates {
		if c.Reaction != nil { // the master's resting observations are not part of the comparison (v7 never emits them)
			out = append(out, c)
		}
	}
	return out
}

// walk replays one capture and hands every closed M5 bar's decisions to visit.
func walk(t testing.TB, doc *config.Document, path string, visit func(b bar, e engine.Evaluation)) *replaycapture.Capture {
	t.Helper()
	capture, err := replaycapture.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	p := newPair(t, doc, capture.Symbol)
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		b := bar{time: e.BarTime, v6: confirmed(p.v6.Evaluate(e.Context)), v7: p.v7.Evaluate(e.Context), refusals: p.v7.TakeRefusals()}
		visit(b, e)
	}})
	if err != nil {
		t.Fatal(err)
	}
	return capture
}

// walkAll runs walk over every capture in parallel; visit is called under a lock.
func walkAll(t testing.TB, visit func(path string, b bar, e engine.Evaluation)) []string {
	t.Helper()
	doc := resolve(t)
	paths := captures(t)
	var mu sync.Mutex
	var wg sync.WaitGroup
	gate := make(chan struct{}, runtime.NumCPU())
	for _, path := range paths {
		wg.Add(1)
		go func(path string) {
			defer wg.Done()
			gate <- struct{}{}
			defer func() { <-gate }()
			walk(t, doc, path, func(b bar, e engine.Evaluation) {
				mu.Lock()
				defer mu.Unlock()
				visit(path, b, e)
			})
		}(path)
	}
	wg.Wait()
	return paths
}

func describe(c opportunity.Candidate) string {
	return fmt.Sprintf("%s %s@%s gap %.3f-%.3f entry %.3f-%.3f", c.Direction, c.Structure.Timeframe, fmt.Sprint(c.Structure.FormedAt), c.Structure.Low, c.Structure.High, c.Entry.Low, c.Entry.High)
}
