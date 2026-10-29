package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// Range Edge, Session Level, the standalone Liquidity Sweep and Fade Scalp were purged: no factory, no catalog entry, and a configuration that
// still names one fails startup instead of reviving it. The factories are exactly the catalog.
func TestPurgedStrategiesHaveNoFactoryAndNoCatalogEntry(t *testing.T) {
	purged := []strategy.StrategyID{"range_edge", "session_level", "liquidity_sweep", "fade_scalp"}
	for _, id := range purged {
		if _, ok := strategyFactories[id]; ok {
			t.Errorf("%s still has a factory", id)
		}
		for _, known := range strategy.KnownIDs() {
			if known == id {
				t.Errorf("%s is still in the catalog", id)
			}
		}
	}
	if len(strategyFactories) != len(strategy.KnownIDs()) {
		t.Fatalf("%d factories for %d catalog entries", len(strategyFactories), len(strategy.KnownIDs()))
	}
	for _, id := range strategy.KnownIDs() {
		if _, ok := strategyFactories[id]; !ok {
			t.Errorf("catalog entry %s has no factory", id)
		}
	}
}

func TestAConfigurationThatStillNamesAPurgedStrategyFailsClosed(t *testing.T) {
	configs := make([]strategy.Config, 0, len(strategy.KnownIDs())+1)
	for _, id := range strategy.KnownIDs() {
		configs = append(configs, strategy.Config{ID: id, Version: "v1", Enabled: true})
	}
	if err := strategy.ValidateConfigs(configs); err != nil {
		t.Fatalf("the catalog itself must validate: %v", err)
	}
	for _, id := range []strategy.StrategyID{"range_edge", "session_level", "liquidity_sweep", "fade_scalp"} {
		stale := append(append([]strategy.Config(nil), configs...), strategy.Config{ID: id, Version: "v1", Enabled: true})
		if err := strategy.ValidateConfigs(stale); err == nil {
			t.Errorf("a configuration naming the purged %s must not validate", id)
		}
	}
}
