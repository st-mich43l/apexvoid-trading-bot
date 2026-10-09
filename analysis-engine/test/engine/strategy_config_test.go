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
	if len(configs) != 21 {
		t.Fatalf("got %d strategy configs, want 21 approved V2 strategies", len(configs))
	}
	// Key Level, the Breakout Retest Scalp and Range Sweep were rebuilt as the
	// profitable-week Python detectors and Liquidity Sweep on the frozen detector
	// contract's evidence (Phase 1), and CRT was rebuilt as a causal H1 sweep, M5
	// reclaim and structure shift (v3); every other strategy is still v2.
	rebuilt := map[string]bool{"crt": true, "key_level": true, "session_level": true, "flip_zone": true, "box_breakout": true, "trendline": true, "impulse_pullback": true, "scalp_breakout_retest": true, "range_sweep": true, "liquidity_sweep": true}
	for _, cfg := range configs {
		want := "v2"
		if rebuilt[string(cfg.ID)] {
			want = "v3"
		}
		if cfg.Version != want {
			t.Errorf("%s: version=%q, want %s", cfg.ID, cfg.Version, want)
		}
		if !cfg.Enabled {
			t.Errorf("%s: enabled=false, want true for the complete shadow catalog", cfg.ID)
		}
	}
}

func TestAll21StrategiesHaveConcreteFactoriesAndValidProductionConfig(t *testing.T) {
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
