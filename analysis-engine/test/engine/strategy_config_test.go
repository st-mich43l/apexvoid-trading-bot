package engine_test

import (
	"fmt"
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
		"key_level": "v6", "supply": "v6", "demand": "v6", "order_block": "v6", "fvg": "v6", "flip_zone": "v5", "trendline": "v6",
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

// order_block v6 (docs/strategies/order-block-definition.md): WithoutOBDefinition is what the frozen-Python parity replays use. It removes exactly the close-break
// rule and the touch limit, leaves every other parameter and strategy alone, and never mutates the caller's map.
func TestWithoutOBDefinitionRemovesOnlyTheDefinitionKeys(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, "M5", false)
	if err != nil {
		t.Fatal(err)
	}
	params := func(s engine.Settings, id string) map[string]any {
		for _, cfg := range s.Strategies {
			if string(cfg.ID) == id {
				return cfg.Parameters
			}
		}
		return nil
	}
	before := params(settings, "order_block")
	if _, ok := before["confirmed_break_buffer_atr"].(float64); !ok {
		t.Fatalf("strategies.order_block must ship confirmed_break_buffer_atr: %v", before)
	}
	if touches := fmt.Sprint(before["maximum_zone_touches"]); touches != "2" {
		t.Fatalf("strategies.order_block must ship maximum_zone_touches: 2, got %v", before["maximum_zone_touches"])
	}
	fvgBefore := len(params(settings, "fvg"))
	size := len(before)
	engine.WithoutOBDefinition(&settings)
	after := params(settings, "order_block")
	if _, ok := after["confirmed_break_buffer_atr"]; ok {
		t.Error("the close-break rule is still there")
	}
	if _, ok := after["maximum_zone_touches"]; ok {
		t.Error("the touch limit is still there")
	}
	if len(after) != size-2 {
		t.Errorf("exactly two keys go: %d -> %d", size, len(after))
	}
	if len(before) != size {
		t.Error("the caller's parameter map was mutated")
	}
	if len(params(settings, "fvg")) != fvgBefore {
		t.Error("another strategy's parameters changed")
	}
}
