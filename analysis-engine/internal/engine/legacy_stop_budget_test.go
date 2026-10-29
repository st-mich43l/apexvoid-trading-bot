package engine

import (
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func budgetSettings(maxPips, minPips, pip float64) *Settings {
	s := &Settings{}
	s.InstrumentStopMaxPips, s.InstrumentStopMinPips = maxPips, minPips
	s.Geometry.PipSize = pip
	for _, id := range []strategy.StrategyID{"key_level", "supply", "demand", "order_block", "fvg", "flip_zone", "trendline", "crt", "ifvg"} {
		s.Strategies = append(s.Strategies, strategy.Config{ID: id, Parameters: map[string]any{"expiry_hours": 12.0}})
	}
	return s
}

func paramsOf(s *Settings, id strategy.StrategyID) map[string]any {
	for _, c := range s.Strategies {
		if c.ID == id {
			return c.Parameters
		}
	}
	return nil
}

func TestLegacyStopBudgetIsInjectedOnlyIntoTheSevenLegacyStrategies(t *testing.T) {
	s := budgetSettings(70, 50, 0.1)
	applyLegacyStopBudget(s)
	for _, id := range []strategy.StrategyID{"key_level", "supply", "demand", "order_block", "fvg", "flip_zone", "trendline"} {
		p := paramsOf(s, id)
		if p["execution_stop_max_pips"] != 70.0 || p["execution_stop_min_pips"] != 50.0 || p["execution_pip_size"] != 0.1 || p["expiry_hours"] != 12.0 {
			t.Errorf("%s: parameters %v", id, p)
		}
	}
	for _, id := range []strategy.StrategyID{"crt", "ifvg"} {
		if _, ok := paramsOf(s, id)["execution_pip_size"]; ok {
			t.Errorf("%s must not receive the legacy stop budget", id)
		}
	}
}

func TestLegacyStopBudgetIsIdempotentAndWaitsForAResolvedInstrument(t *testing.T) {
	unresolved := budgetSettings(0, 0, 0.1)
	applyLegacyStopBudget(unresolved)
	if _, ok := paramsOf(unresolved, "key_level")["execution_stop_max_pips"]; ok {
		t.Fatal("zero must not read as a cap of zero pips")
	}
	noPip := budgetSettings(70, 50, 0)
	applyLegacyStopBudget(noPip)
	if _, ok := paramsOf(noPip, "supply")["execution_stop_max_pips"]; ok {
		t.Fatal("a budget without a pip size cannot be measured")
	}
	once, twice := budgetSettings(20, 12, 0.0001), budgetSettings(20, 12, 0.0001)
	applyLegacyStopBudget(once)
	applyLegacyStopBudget(twice)
	applyLegacyStopBudget(twice)
	if !reflect.DeepEqual(once.Strategies, twice.Strategies) {
		t.Fatal("applying the budget twice changed the parameters")
	}
	noFloor := budgetSettings(20, 0, 0.0001)
	applyLegacyStopBudget(noFloor)
	if _, ok := paramsOf(noFloor, "demand")["execution_stop_min_pips"]; ok {
		t.Fatal("an absent floor must not be injected as zero")
	}
}

func TestLegacyStopBudgetDoesNotMutateTheCallersParameterMaps(t *testing.T) {
	s := budgetSettings(70, 50, 0.1)
	original := s.Strategies[0].Parameters
	applyLegacyStopBudget(s)
	if _, ok := original["execution_stop_max_pips"]; ok {
		t.Fatal("the original parameter map was mutated")
	}
}

func TestWithoutLegacyStopBudgetRestoresTheUnbudgetedStrategies(t *testing.T) {
	s := budgetSettings(70, 50, 0.1)
	applyLegacyStopBudget(s)
	WithoutLegacyStopBudget(s)
	for _, id := range []strategy.StrategyID{"key_level", "supply", "trendline"} {
		if p := paramsOf(s, id); !reflect.DeepEqual(p, map[string]any{"expiry_hours": 12.0}) {
			t.Errorf("%s: %v", id, p)
		}
	}
}
