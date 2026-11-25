package engine

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/state"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
)

// Key Level v11 ships its structure-close buffer (0.0: any M5 close beyond the band) and keeps the 24 h entry window, and a malformed value does not build.
func TestKeyLevelShipsTheStructureCloseRule(t *testing.T) {
	settings := settingsFor(t, copyConfig(t, "false"))
	p := strategyParams(t, settings, "key_level")
	if v, ok := p["structure_close_buffer_atr"].(float64); !ok || v != 0 {
		t.Fatalf("structure_close_buffer_atr must ship as 0.0, got %#v", p["structure_close_buffer_atr"])
	}
	if p["expiry_hours"] != 24.0 {
		t.Fatalf("expiry_hours must stay 24.0 (Q1 did not separate outcomes by wait), got %v", p["expiry_hours"])
	}
	var cfg strategy.Config
	for _, c := range settings.Strategies {
		if c.ID == "key_level" {
			cfg = c
		}
	}
	if cfg.Version != "v14" {
		t.Fatalf("key_level version %q, want v14", cfg.Version)
	}
	if _, err := keylevel.New(cfg); err != nil {
		t.Fatalf("the shipped Key Level config must build: %v", err)
	}
	for name, mutate := range map[string]func(map[string]any){
		"negative": func(m map[string]any) { m["structure_close_buffer_atr"] = -0.1 },
		"NaN":      func(m map[string]any) { m["structure_close_buffer_atr"] = math.NaN() },
		"missing":  func(m map[string]any) { delete(m, "structure_close_buffer_atr") },
	} {
		params := map[string]any{}
		for k, v := range cfg.Parameters {
			params[k] = v
		}
		mutate(params)
		bad := cfg
		bad.Parameters = params
		if _, err := keylevel.New(bad); err == nil {
			t.Errorf("%s structure_close_buffer_atr was accepted", name)
		}
	}
}

// Order Block's rule is unchanged and independent of Key Level's: each strategy's buffer is its own, and turning one off never turns the other off.
func TestStructureBreakBuffersAreIndependentPerStrategy(t *testing.T) {
	real := settingsFor(t, copyConfig(t, "false"))
	if v, on := strategyBreakBufferATR(real, "order_block"); !on || v != strategyParams(t, real, "order_block")["confirmed_break_buffer_atr"] {
		t.Fatalf("order_block: %v %v", v, on)
	}
	if v, on := strategyBreakBufferATR(real, "key_level"); !on || v != 0 {
		t.Fatalf("key_level: %v %v", v, on)
	}
	if _, on := strategyBreakBufferATR(real, "supply"); on {
		t.Fatal("a strategy without the rule must have no buffer")
	}
	orderBlockOff := Settings{Strategies: []strategy.Config{
		{ID: "order_block", Parameters: map[string]any{"confirmed_break_buffer_atr": 0.0}},
		{ID: "key_level", Parameters: map[string]any{"structure_close_buffer_atr": 0.5}},
	}}
	if _, on := strategyBreakBufferATR(orderBlockOff, "order_block"); on {
		t.Fatal("an order-block buffer of 0 is the rule off, as before")
	}
	if v, on := strategyBreakBufferATR(orderBlockOff, "key_level"); !on || v != 0.5 {
		t.Fatalf("Key Level must still retire with the order-block buffer at 0: %v %v", v, on)
	}
	keyLevelOnly := Settings{Strategies: []strategy.Config{{ID: "key_level", Parameters: map[string]any{"structure_close_buffer_atr": 0.0}}}}
	if _, on := strategyBreakBufferATR(keyLevelOnly, "order_block"); on {
		t.Fatal("Key Level's key must not switch the order-block rule on")
	}
}

// The worker multiplies the strategy's ATR count by the closed frame's ATR: 0.5 x 2.0 = 1.0 price units for Key Level, 0 for a zero buffer, none for others.
func TestWorkerStructureBreakBufferIsInPriceUnits(t *testing.T) {
	settings := Settings{Strategies: []strategy.Config{
		{ID: "order_block", Parameters: map[string]any{"confirmed_break_buffer_atr": 0.1}},
		{ID: "key_level", Parameters: map[string]any{"structure_close_buffer_atr": 0.5}},
	}}
	w := &SymbolWorker{settings: settings, state: &state.SymbolState{Context: context.MarketContext{Timeframes: map[market.Timeframe]*context.TimeframeContext{
		market.M5: {Legacy: &context.LegacyFrame{ATR: []float64{1, 2}}},
	}}}}
	buffer := w.structureBreakBuffer(market.M5, nil)
	if buffer == nil {
		t.Fatal("a configured rule must give a buffer")
	}
	if v, ok := buffer("key_level"); !ok || math.Abs(v-1.0) > 1e-12 {
		t.Fatalf("key_level buffer %v %v, want 1.0", v, ok)
	}
	if v, ok := buffer("order_block"); !ok || math.Abs(v-0.2) > 1e-12 {
		t.Fatalf("order_block buffer %v %v, want 0.2", v, ok)
	}
	if _, ok := buffer(opportunity.StrategyID("supply")); ok {
		t.Fatal("supply has no structure-close rule")
	}
	if w.structureBreakBuffer(market.M5, nil) == nil || (&SymbolWorker{settings: settings, state: &state.SymbolState{}}).structureBreakBuffer(market.M5, nil) != nil {
		t.Fatal("a frame with no ATR gives no buffer")
	}
}
