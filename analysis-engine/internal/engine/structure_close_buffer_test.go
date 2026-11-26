package engine

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// Q0: every strategy may declare its own structure_close_buffer_atr; absent means no structure-close rule. The shipped configuration declares it for key_level, flip_zone (Q3) and
// (as confirmed_break_buffer_atr) order_block; each further strategy PR adds its own.
func TestStructureCloseBufferIsOpenToEveryStrategyAndOffWhereAbsent(t *testing.T) {
	settings := Settings{Strategies: []strategy.Config{
		{ID: "supply", Parameters: map[string]any{"structure_close_buffer_atr": 0.2}},
		{ID: "demand", Parameters: map[string]any{"structure_close_buffer_atr": 0.0}},
		{ID: "fvg", Parameters: map[string]any{}},
		{ID: "flip_zone", Parameters: map[string]any{"structure_close_buffer_atr": -0.1}},
		{ID: "ifvg", Parameters: map[string]any{"structure_close_buffer_atr": "0.2"}},
	}}
	if v, on := strategyBreakBufferATR(settings, "supply"); !on || v != 0.2 {
		t.Fatalf("supply: %v %v", v, on)
	}
	if v, on := strategyBreakBufferATR(settings, "demand"); !on || v != 0 {
		t.Fatalf("demand with a 0 buffer: any close beyond the edge, got %v %v", v, on)
	}
	for _, id := range []strategy.StrategyID{"fvg", "flip_zone", "ifvg", "nobody"} {
		if _, on := strategyBreakBufferATR(settings, id); on {
			t.Fatalf("%s must have no structure-close rule (absent, negative or not a number)", id)
		}
	}
}

func TestTheShippedConfigurationDeclaresTheStructureCloseRuleOnlyWhereTheStrategyAdoptedIt(t *testing.T) {
	settings := settingsFor(t, filepath.Join(configDir, "apexvoid.yml"))
	on := map[strategy.StrategyID]bool{}
	for _, cfg := range settings.Strategies {
		if _, active := strategyBreakBufferATR(settings, cfg.ID); active {
			on[cfg.ID] = true
		}
	}
	if len(on) != 5 || !on["order_block"] || !on["key_level"] || !on["flip_zone"] || !on["break_retest"] || !on["crt"] {
		t.Fatalf("only order_block, key_level, flip_zone (Q3), break_retest (Q4) and crt (CRT4: candle 2's close) carry a structure-close rule today, got %v", on)
	}
}
