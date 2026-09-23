package engine_test

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
)

// enabledSinceS7 is exactly the set Phase S7 implemented a real factory
// for and flipped to enabled: true in config/analysis.yml — see
// docs/analysis/strategy-v2-catalog.md's "Phase S7 status" for why each
// of the other 12 stays disabled.
var enabledSinceS7 = map[string]bool{
	"key_level": true, "session_level": true, "supply": true, "demand": true,
	"order_block": true, "fvg": true, "flip_zone": true,
}

func TestStrategyConfigsFromConfigMatchesThePhaseS7CatalogState(t *testing.T) {
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
		want := enabledSinceS7[string(cfg.ID)]
		if cfg.Enabled != want {
			t.Errorf("%s: enabled=%t, want %t (Phase S7 catalog state)", cfg.ID, cfg.Enabled, want)
		}
	}
}
