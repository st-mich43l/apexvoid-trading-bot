package techniqueparity_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Deliberate departure from the frozen Python oracle (strategy independence).
//
// The frozen publishers skipped every technique instance a confluence band covered, so a
// valid FVG / Order Block / Supply / Demand setup vanished whenever Confluence Zone
// overlapped it. Production now evaluates each technique on its own instances; Confluence
// Zone aggregates the same canonical facts into its own opportunity beside them. The oracle
// golden above is untouched and still proven bar for bar through
// TechniqueExcludingConfluenceCoverage; this test pins the intended new behavior.
//
// For every bar of every committed capture:
//   - a technique the oracle would publish is still published (independence can only add);
//   - a bar the oracle silenced only because a band covered the instance now publishes the
//     technique's own confirmed setup;
//   - the Confluence Zone decision is the same whatever the techniques do.
func TestConfluenceCoverageNeverSuppressesAnIndependentTechniqueCandidate(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "testdata", "technique-parity-oracle.json"))
	if err != nil {
		t.Fatal(err)
	}
	var g golden
	if err := json.Unmarshal(data, &g); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	techniques := []string{
		strategyutil.TechniqueSupplyDemand, strategyutil.TechniqueOrderBlock, strategyutil.TechniqueFVG,
		strategyutil.TechniqueIFVG, strategyutil.TechniqueCRT,
	}
	recovered := map[string]int{}
	published := map[string]int{}
	var symbols []string
	for symbol := range g.Symbols {
		symbols = append(symbols, symbol)
	}
	sort.Strings(symbols)
	for _, symbol := range symbols {
		settings, err := engine.LoadSettings(doc, market.M5, false)
		if err != nil {
			t.Fatal(err)
		}
		if err := engine.ApplyInstrument(&settings, doc, symbol); err != nil {
			t.Fatal(err)
		}
		var legacy strategyutil.LegacyDetectorSettings
		for _, cfg := range settings.Strategies {
			if cfg.ID == "break_retest" {
				if legacy, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
					t.Fatal(err)
				}
			}
		}
		capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", g.Symbols[symbol].Capture))
		if err != nil {
			t.Fatal(err)
		}
		_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
			if e.Timeframe != market.M5 {
				return
			}
			source, ok := strategyutil.NewTechniqueSource(e.Context, market.M5, legacy)
			if !ok {
				return
			}
			for _, technique := range techniques {
				oracle := source.TechniqueExcludingConfluenceCoverage(technique)
				own := source.Technique(technique)
				if oracle != nil && own == nil {
					t.Errorf("%s %s bar %d: the oracle publishes it, independence removed it", symbol, technique, e.BarTime)
				}
				if own != nil {
					published[technique]++
				}
				if oracle == nil && own != nil {
					recovered[technique]++
				}
			}
		}})
		if err != nil {
			t.Fatal(err)
		}
	}
	total := 0
	for _, technique := range techniques {
		t.Logf("%-14s published on %5d bars, %4d of them previously suppressed by a confluence band", technique, published[technique], recovered[technique])
		total += recovered[technique]
	}
	if total == 0 {
		t.Fatal("no bar in the captures had a confluence band covering a confirmed technique, so the test proves nothing")
	}
}
