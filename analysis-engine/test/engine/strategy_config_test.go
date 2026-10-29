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
	if len(configs) != 12 {
		t.Fatalf("got %d strategy configs, want 12 approved strategies", len(configs))
	}
	// Versions are published geometry: a strategy whose invalidation, target or entry changes gets a new version, because the version is part of the
	// opportunity id. The seven legacy-family strategies moved on 2026-10-24 when their invalidation was anchored beyond the reaction's own extreme and again when the instrument stop budget was applied.
	versions := map[string]string{
		"key_level": "v6", "supply": "v6", "demand": "v6", "order_block": "v5", "fvg": "v6", "flip_zone": "v5", "trendline": "v6",
		"crt": "v3", "impulse_pullback": "v3", "break_retest": "v3", "ifvg": "v3", "confluence_zone": "v3",
	}
	for _, cfg := range configs {
		want := versions[string(cfg.ID)]
		if cfg.Version != want {
			t.Errorf("%s: version=%q, want %s", cfg.ID, cfg.Version, want)
		}
		if !cfg.Enabled {
			t.Errorf("%s: enabled=false, want true for the complete shadow catalog", cfg.ID)
		}
	}
}

func TestAll20StrategiesHaveConcreteFactoriesAndValidProductionConfig(t *testing.T) {
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
		t.Fatalf("the complete 21-strategy catalog must construct from canonical config: %v", err)
	}
}

// Fade Scalp was purged (CHANGELOG.md, 2026-10-29): a rendered configuration that still carries its block is a deployment error and
// must stop the engine at startup, never revive the strategy or be ignored.
func TestAConfigurationThatStillNamesFadeScalpFailsStartup(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	strategies := doc.Raw()["analysis"].(map[string]any)["strategies"].(map[string]any)
	if _, present := strategies["fade_scalp"]; present {
		t.Fatal("the committed configuration must not carry strategies.fade_scalp")
	}
	strategies["fade_scalp"] = map[string]any{"version": "v3", "enabled": true}
	if _, err := engine.StrategyConfigsFromConfig(doc); err == nil {
		t.Fatal("a configuration naming fade_scalp must fail startup")
	}
}
