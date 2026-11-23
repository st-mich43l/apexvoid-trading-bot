// Package architecture holds the repository-structure tests of the analysis engine.
package architecture

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// Owner rule (23 Nov 2026): every strategy finds its setup on M5, M15 and H1; M1 is EXECUTION ONLY. It may time a fill after a setup is published, it never finds or
// confirms one. This test holds the rule for every strategy.
//
// m1Exceptions are the strategies that still break it, named so the debt is visible and bounded. Impulse Pullback confirms on M1 until PR-Q11 moves it to M5; Q11
// deletes its entry here, and the test fails if an entry outlives its violation, so an exception can never be forgotten.
// TODO(PR-Q11): remove "impulse_pullback" from m1Exceptions.
var m1Exceptions = map[string]bool{"impulse_pullback": true}

const configRoot = "../../../config/apexvoid.yml"

func TestNoStrategyRequiresM1(t *testing.T) {
	doc, err := config.ResolveDocument(configRoot)
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, "XAU"); err != nil {
		t.Fatal(err)
	}
	factories := engine.StrategyFactories()
	ids := make([]string, 0, len(factories))
	for id := range factories {
		ids = append(ids, string(id))
	}
	sort.Strings(ids)
	byID := map[string]strategy.Config{}
	for _, cfg := range settings.Strategies {
		byID[string(cfg.ID)] = cfg
	}
	for _, id := range ids {
		cfg, ok := byID[id]
		if !ok {
			t.Fatalf("strategy %q has a factory but no configuration", id)
		}
		cfg.Enabled = true
		instance, err := factories[strategy.StrategyID(id)](cfg)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		requiresM1 := false
		for _, tf := range instance.RequiredTimeframes() {
			if tf == market.M1 {
				requiresM1 = true
			}
		}
		switch {
		case requiresM1 && !m1Exceptions[id]:
			t.Errorf("%s RequiredTimeframes contains M1: M1 is execution only (owner rule 23 Nov 2026)", id)
		case !requiresM1 && m1Exceptions[id]:
			t.Errorf("%s no longer requires M1: delete it from m1Exceptions (the Q11 exception is over)", id)
		}
	}
}

func TestNoStrategyPackageReadsM1OutsideExecutionFiles(t *testing.T) {
	root := "../../internal/strategy"
	fset := token.NewFileSet()
	found := map[string]bool{} // package directory -> reads market.M1
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		if strings.HasPrefix(filepath.Base(path), "execution") {
			return nil // the one place M1 may appear: timing a fill after publication
		}
		file, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			return err
		}
		ast.Inspect(file, func(n ast.Node) bool {
			sel, ok := n.(*ast.SelectorExpr)
			if !ok || sel.Sel.Name != "M1" {
				return true
			}
			if pkg, ok := sel.X.(*ast.Ident); ok && pkg.Name == "market" {
				rel, _ := filepath.Rel(root, filepath.Dir(path))
				found[rel] = true
				if !m1Exceptions[strings.ReplaceAll(rel, "impulsepullback", "impulse_pullback")] {
					t.Errorf("%s reads market.M1 outside an execution*.go file (M1 is execution only): %s", rel, fset.Position(sel.Pos()))
				}
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for id := range m1Exceptions {
		if !found[strings.ReplaceAll(id, "_", "")] {
			t.Errorf("%s no longer reads market.M1: delete it from m1Exceptions (the Q11 exception is over)", id)
		}
	}
}
