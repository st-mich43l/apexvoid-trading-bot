package strategy_test

import (
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const strategyPackages = "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/"

// TestNoConcreteStrategyImportsAnotherStrategy: 20 strategies, 20 independent
// technical decisions. A strategy may use the shared market intelligence layer
// (context, market, techniquezone, strategyutil and the like) but never another
// strategy's package, so none can call, wrap or depend on another's decision.
func TestNoConcreteStrategyImportsAnotherStrategy(t *testing.T) {
	dirs, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	checked := 0
	for _, dir := range dirs {
		if !dir.IsDir() {
			continue
		}
		files, _ := filepath.Glob(filepath.Join(dir.Name(), "*.go"))
		for _, file := range files {
			parsed, err := parser.ParseFile(token.NewFileSet(), file, nil, parser.ImportsOnly)
			if err != nil {
				t.Fatal(err)
			}
			for _, imp := range parsed.Imports {
				path := strings.Trim(imp.Path.Value, `"`)
				if strings.HasPrefix(path, strategyPackages) {
					t.Errorf("%s imports another strategy package %s", file, path)
				}
			}
		}
		checked++
	}
	if checked != 12 {
		t.Fatalf("checked %d strategy packages, want 12", checked)
	}
}
