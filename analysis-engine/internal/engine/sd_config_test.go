package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/demand"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/supply"
)

var sdFactories = map[strategy.StrategyID]strategy.Factory{"supply": supply.New, "demand": demand.New}

func strategyParams(t *testing.T, settings Settings, id strategy.StrategyID) map[string]any {
	t.Helper()
	for _, s := range settings.Strategies {
		if s.ID == id {
			return s.Parameters
		}
	}
	t.Fatalf("strategy %s is not configured", id)
	return nil
}

// The shipped configuration turns the swing-anchored zones and the entry candidates on, keeps XAU's published entry zone at 30 pips and hands that minimum to the
// planner (it never offers an entry whose band would be narrower), and leaves the candle-confirmation requirement and quality ranking exactly as they were.
func TestSupplyAndDemandShipTheSwingModelWithTheInstrumentMinimumWidth(t *testing.T) {
	settings := settingsFor(t, copyConfig(t, "false"))
	if settings.RequireCandleConfirmation {
		t.Fatal("analysis.require_candle_confirmation must stay false")
	}
	if !settings.Arbitration.IgnoreQuality {
		t.Fatal("quality ranking must stay off")
	}
	if settings.InstrumentEntryZoneMinimumWidthPips != 30 {
		t.Fatalf("XAU's non-scalp entry zone minimum is 30 pips, got %v", settings.InstrumentEntryZoneMinimumWidthPips)
	}
	for _, id := range []strategy.StrategyID{"supply", "demand"} {
		p := strategyParams(t, settings, id)
		if p["zone_model"] != "swing" || p["entry_model"] != "candidates" || p["minimum_zone_strength"] != "moderate" {
			t.Fatalf("%s: zone_model %v entry_model %v minimum_zone_strength %v", id, p["zone_model"], p["entry_model"], p["minimum_zone_strength"])
		}
		if p["entry_zone_minimum_width_pips"] != 30.0 {
			t.Fatalf("%s: the planner must receive the instrument's 30-pip minimum, got %v", id, p["entry_zone_minimum_width_pips"])
		}
	}
}

// The rollback is two leaves: either one restores the earlier behaviour on its own, and the strategy still builds.
func TestTheSwingModelHasAnExplicitRollback(t *testing.T) {
	settings := settingsFor(t, copyConfig(t, "false"))
	for _, rollback := range []map[string]any{{"zone_model": "legacy"}, {"entry_model": "proximal"}, {"zone_model": "legacy", "entry_model": "proximal"}} {
		for _, id := range []strategy.StrategyID{"supply", "demand"} {
			params := map[string]any{}
			for k, v := range strategyParams(t, settings, id) {
				params[k] = v
			}
			for k, v := range rollback {
				params[k] = v
			}
			if _, err := sdFactories[id](strategy.Config{ID: id, Version: "x", Enabled: true, Parameters: params}); err != nil {
				t.Fatalf("%s with %v: %v", id, rollback, err)
			}
		}
	}
	bad := map[string]any{}
	for k, v := range strategyParams(t, settings, "supply") {
		bad[k] = v
	}
	bad["zone_model"] = "cloud"
	if _, err := sdFactories["supply"](strategy.Config{ID: "supply", Version: "x", Enabled: true, Parameters: bad}); err == nil {
		t.Fatal("an unknown zone_model must fail startup, never fall back silently")
	}
}

// The classifier reads only the frame's window: a swing must be relevant for no longer than the window can still show its left side and the leg it came from, or a class would
// change because bars were evicted rather than because the market did.
func TestSwingRelevanceFitsTheStructureFrameWindows(t *testing.T) {
	doc, err := config.ResolveDocument(configDir + "/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []strategy.StrategyID{"supply", "demand"} {
		settings := settingsFor(t, copyConfig(t, "false"))
		p := strategyParams(t, settings, id)
		relevance, external := p["swing_relevance_bars"].(int), p["swing_external_bars"].(int)
		frames, _ := p["structure_timeframes"].([]any)
		for _, f := range frames {
			window, err := getInt(doc, "analysis.legacy_read.window_bars."+f.(string))
			if err != nil {
				t.Fatal(err)
			}
			if relevance+external > window {
				t.Fatalf("%s %s: swing_relevance_bars %d + swing_external_bars %d exceeds the %d-bar window", id, f, relevance, external, window)
			}
		}
	}
}
