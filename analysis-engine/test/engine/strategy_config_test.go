package engine_test

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
)

func TestStrategyConfigsFromConfigEnablesTheCompleteCatalogForShadow(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	configs, err := engine.StrategyConfigsFromConfig(doc)
	if err != nil {
		t.Fatal(err)
	}
	if len(configs) != 19 {
		t.Fatalf("got %d strategy configs, want 19 approved V2 strategies", len(configs))
	}
	for _, cfg := range configs {
		if cfg.Version != "v2" {
			t.Errorf("%s: version=%q, want v2", cfg.ID, cfg.Version)
		}
		if !cfg.Enabled {
			t.Errorf("%s: enabled=false, want true for the complete shadow catalog", cfg.ID)
		}
	}
}

func TestAll19StrategiesHaveConcreteFactoriesAndValidProductionConfig(t *testing.T) {
	repoRoot := filepath.Join("..", "..", "..")
	doc, err := config.ResolveDocument(filepath.Join(repoRoot, "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, "M5", false)
	if err != nil {
		t.Fatal(err)
	}
	for i := range settings.Strategies {
		settings.Strategies[i].Enabled = true
	}
	if _, err := engine.NewSymbolWorker("XAU", settings, nil, nil); err != nil {
		t.Fatalf("the complete 19-strategy catalog must construct from canonical config: %v", err)
	}
}
