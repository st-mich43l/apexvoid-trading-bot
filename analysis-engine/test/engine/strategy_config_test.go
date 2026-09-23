package engine_test

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
)

func TestStrategyConfigsFromConfigRequiresTheCompleteDisabledUntilImplementedCatalog(t *testing.T) {
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
		if cfg.Version != "v2" || cfg.Enabled {
			t.Errorf("%s = version=%q enabled=%t, want v2/false before Phase S7", cfg.ID, cfg.Version, cfg.Enabled)
		}
	}
}
