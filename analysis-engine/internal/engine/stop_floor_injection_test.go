package engine

import (
	"math"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/fvg"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/orderblock"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// FX3: FVG v12 and Order Block v12 judge room against StopBudget.EffectiveRisk, which differs from v11 only when the engine hands the strategy the instrument's stop
// floor (applyLegacyStopBudget: execution_stop_min_pips and execution_pip_size). The strategies' own room tests build their Config by hand, so this is the test that
// fails if the engine ever stops injecting the floor, which would turn v12 back into v11 without any other test noticing.
func TestTheInstrumentStopFloorReachesTheStrategiesWhoseRoomDependsOnIt(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join(configDir, "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	factories := map[strategy.StrategyID]strategy.Factory{"fvg": fvg.New, "order_block": orderblock.New, "key_level": keylevel.New}
	for _, symbol := range []string{"EURUSD", "GBPUSD", "USDJPY", "GBPJPY", "XAU"} {
		t.Run(symbol, func(t *testing.T) {
			// The expectation comes from the configuration itself (instruments.yml stop_envelope and the instrument's pip size), not from the settings under test.
			wantFloor, _, err := doc.StopEnvelopeFor(symbol)
			if err != nil {
				t.Fatal(err)
			}
			geometry, err := doc.GeometryFor(symbol)
			if err != nil {
				t.Fatal(err)
			}
			if !(wantFloor > 0) || !(geometry.PipSize > 0) {
				t.Fatalf("%s: the configuration must declare a stop floor and a pip size, got floor %v pip %v", symbol, wantFloor, geometry.PipSize)
			}
			settings, err := LoadSettings(doc, market.M5, false)
			if err != nil {
				t.Fatal(err)
			}
			if err := ApplyInstrument(&settings, doc, symbol); err != nil {
				t.Fatal(err)
			}
			seen := map[strategy.StrategyID]bool{}
			for _, s := range settings.Strategies {
				factory, ok := factories[s.ID]
				if !ok {
					continue
				}
				seen[s.ID] = true
				if got, present := s.Parameters["execution_stop_min_pips"]; !present || got != wantFloor {
					t.Errorf("%s %s: execution_stop_min_pips = %v (present %v), want the instrument floor %v", symbol, s.ID, got, present, wantFloor)
				}
				if got, present := s.Parameters["execution_pip_size"]; !present || got != geometry.PipSize {
					t.Errorf("%s %s: execution_pip_size = %v (present %v), want %v", symbol, s.ID, got, present, geometry.PipSize)
				}
				budget, err := strategyutil.ParseStopBudget(s.Parameters)
				if err != nil {
					t.Fatalf("%s %s: %v", symbol, s.ID, err)
				}
				if budget.MinPips != wantFloor || budget.PipSize != geometry.PipSize || !budget.Active() {
					t.Errorf("%s %s: parsed budget %+v, want MinPips %v PipSize %v active", symbol, s.ID, budget, wantFloor, geometry.PipSize)
				}
				// A floor-widened stop is what the budget turns into a larger effective risk: one pip under the floor reads as the floor, never as itself.
				reference := 100.0
				structural := reference - (wantFloor-1)*geometry.PipSize
				if wantFloor > 1 {
					if got, want := budget.EffectiveRisk(reference, structural), wantFloor*geometry.PipSize; math.Abs(got-want) > 1e-9 {
						t.Errorf("%s %s: effective risk of a stop one pip under the floor = %v, want the floor %v", symbol, s.ID, got, want)
					}
				}
				if _, err := factory(strategy.Config{ID: s.ID, Version: "x", Enabled: true, Parameters: s.Parameters}); err != nil {
					t.Errorf("%s %s: the strategy does not build with the injected budget: %v", symbol, s.ID, err)
				}
			}
			for id := range factories {
				if !seen[id] {
					t.Errorf("%s: strategy %s is not configured", symbol, id)
				}
			}
		})
	}
}
