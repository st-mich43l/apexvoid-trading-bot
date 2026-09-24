package zone_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func zoneTestConfig() zone.Config {
	return zone.Config{
		Displacement: displacementConfigForTest(),
		Lifecycle: zone.LifecycleConfig{
			InvalidationToleranceATR: 0.5,
			SweepReclaimBars:         2,
			MaxBreakEpisodes:         2,
			RetestMaxTouches:         3,
			EpsilonATR:               0.05,
		},
		Relevance:              zone.RelevanceConfig{ImmediateATR: 0.25, NearbyATR: 1.25, RemoteATR: 3.0},
		FlipAcceptBars:         2,
		FlipBandBodyFraction:   0.5,
		FlipLevelBandATR:       0.05,
		OrderBlockBodyFraction: 0.55,
	}
}

// displacementConfigForTest keeps the fixture explicit without coupling
// the test to any production config loader.
func displacementConfigForTest() structure.DisplacementConfig {
	return structure.DisplacementConfig{RangeATR: 1.5, BodyDominance: 0.55}
}

func c(t int64, open, high, low, close float64) market.Candle {
	return market.Candle{Time: t, Open: open, High: high, Low: low, Close: close, Volume: 1}
}

func TestUpdateBuildsFVGWithFreshStateAndIndependentRelevance(t *testing.T) {
	candles := []market.Candle{
		c(1, 100, 101, 99, 100),
		c(2, 100, 101, 99, 100),
		c(3, 101, 103, 102, 102.5),
	}
	state := zone.Update(candles, []float64{1, 1, 1}, nil, nil, "M5", zoneTestConfig())

	if len(state.Zones) == 0 {
		t.Fatal("expected the three-candle sequence to produce a zone")
	}
	var found *zone.Zone
	for i := range state.Zones {
		if state.Zones[i].Kind == zone.KindFVG {
			found = &state.Zones[i]
			break
		}
	}
	if found == nil {
		t.Fatal("expected a demand FVG")
	}
	if found.Side != zone.Demand || found.Low != 101 || found.High != 102 {
		t.Fatalf("unexpected FVG geometry: %+v", *found)
	}
	if found.State != zone.StateFresh {
		t.Errorf("new FVG should be fresh, got %s", found.State)
	}
	if found.Relevance != zone.Nearby {
		t.Errorf("FVG half an ATR away should be nearby, got %s", found.Relevance)
	}
	if found.Strength < 0.4 {
		t.Errorf("objective gap/displacement evidence should clear the configured strategy floor, got %.3f", found.Strength)
	}
}

func TestFVGStrengthDecaysWithFillAndDropsToZeroWhenInvalidated(t *testing.T) {
	formation := []market.Candle{
		c(1, 100, 101, 99, 100),
		c(2, 100, 102, 100, 102),
		c(3, 102, 103, 102, 102.5),
	}
	fresh := zone.Update(formation, []float64{1, 1, 1}, nil, nil, "M5", zoneTestConfig())
	freshStrength := strengthOf(t, fresh, zone.KindFVG, 101, 102)
	partialBars := append(append([]market.Candle(nil), formation...), c(4, 102.4, 102.5, 101.5, 102.2))
	partial := zone.Update(partialBars, []float64{1, 1, 1, 1}, nil, nil, "M5", zoneTestConfig())
	partialStrength := strengthOf(t, partial, zone.KindFVG, 101, 102)
	if !(partialStrength > 0 && partialStrength < freshStrength) {
		t.Fatalf("partial fill must decay but preserve strength: fresh=%.3f partial=%.3f", freshStrength, partialStrength)
	}
	invalidBars := append(partialBars, c(5, 101.8, 102, 99, 99.5), c(6, 99.5, 100, 98, 99))
	invalid := zone.Update(invalidBars, []float64{1, 1, 1, 1, 1, 1}, nil, nil, "M5", zoneTestConfig())
	if got := strengthOf(t, invalid, zone.KindFVG, 101, 102); got != 0 {
		t.Fatalf("invalidated FVG must have zero usable strength, got %.3f", got)
	}
}

func strengthOf(t *testing.T, state zone.ZoneState, kind zone.Kind, low, high float64) float64 {
	t.Helper()
	for _, candidate := range state.Zones {
		if candidate.Kind == kind && float64(candidate.Low) == low && float64(candidate.High) == high {
			return candidate.Strength
		}
	}
	t.Fatalf("zone %s %.2f-%.2f not found in %+v", kind, low, high, state.Zones)
	return 0
}

func TestIFVGRequiresACloseThroughTheGapAndKeepsItsOwnIdentity(t *testing.T) {
	candles := []market.Candle{
		c(1, 100, 101, 99, 100),
		c(2, 100, 101, 99, 100),
		c(3, 101, 103, 102, 102.5),
		c(4, 102.5, 102.8, 99.5, 100),
		c(5, 100, 100.5, 98.5, 99),
	}
	state := zone.Update(candles, []float64{1, 1, 1, 1, 1}, nil, nil, "M5", zoneTestConfig())

	var fvg, ifvg *zone.Zone
	for i := range state.Zones {
		switch state.Zones[i].Kind {
		case zone.KindFVG:
			if state.Zones[i].Low == 101 && state.Zones[i].High == 102 {
				fvg = &state.Zones[i]
			}
		case zone.KindIFVG:
			if fvg != nil && state.Zones[i].DisplacementRef == fvg.ID {
				ifvg = &state.Zones[i]
			}
		}
	}
	if fvg != nil && ifvg == nil {
		for i := range state.Zones {
			if state.Zones[i].Kind == zone.KindIFVG && state.Zones[i].DisplacementRef == fvg.ID {
				ifvg = &state.Zones[i]
				break
			}
		}
	}
	if fvg == nil || ifvg == nil {
		t.Fatalf("expected both FVG and iFVG, got %+v", state.Zones)
	}
	if ifvg.Side != zone.Supply || ifvg.DisplacementRef != fvg.ID {
		t.Errorf("iFVG must invert the original and retain provenance: fvg=%+v ifvg=%+v", *fvg, *ifvg)
	}
}

func TestLifecycleForgivesReclaimedSweepButInvalidatesUnreclaimedBreak(t *testing.T) {
	cfg := zoneTestConfig().Lifecycle
	z := zone.Zone{Side: zone.Demand, Low: 100, High: 102}

	reclaimed := []market.Candle{
		c(1, 102, 102.5, 98.5, 99),
		c(2, 99, 101, 98.8, 100.2),
	}
	if !zone.NotInvalidated(reclaimed, 0, z, 1, cfg) {
		t.Fatal("a breach reclaimed within the configured window must remain valid")
	}
	if got := zone.DeriveState(reclaimed, 0, z, 1, 1, cfg); got != zone.StateTouched {
		t.Fatalf("expected touched valid zone after reclaimed sweep, got %s", got)
	}

	unreclaimed := append(reclaimed[:1], c(2, 99, 99.5, 97, 98))
	if zone.NotInvalidated(unreclaimed, 0, z, 1, cfg) {
		t.Fatal("an unreclaimed close beyond the far edge must invalidate")
	}
	if got := zone.DeriveState(unreclaimed, 0, z, 1, 1, cfg); got != zone.StateInvalidated {
		t.Fatalf("expected invalidated zone, got %s", got)
	}
}

func TestLifecycleSeparatesTouchExhaustionFromStructuralInvalidation(t *testing.T) {
	cfg := zoneTestConfig().Lifecycle
	z := zone.Zone{Side: zone.Supply, Low: 100, High: 102}
	valid := []market.Candle{c(1, 101, 102, 100, 101)}

	if got := zone.DeriveState(valid, 0, z, 4, 1, cfg); got != zone.StateMitigated {
		t.Fatalf("expected retest exhaustion to be mitigated, got %s", got)
	}
	if got := zone.DeriveState(valid, 0, z, 2, 1, cfg); got != zone.StatePartiallyMitigated {
		t.Fatalf("expected a still-valid multi-touch zone to be partially mitigated, got %s", got)
	}
}

func TestOrderBlockRequiresConfirmingBreakBodyQuality(t *testing.T) {
	weak := c(1, 100, 110, 99, 101)
	if zone.QualifyingBreakBody(weak, 0.55) {
		t.Fatal("a wick-dominated confirming candle must not qualify an order block")
	}
	strong := c(1, 100, 102, 99, 101.8)
	if !zone.QualifyingBreakBody(strong, 0.55) {
		t.Fatal("a body-dominant confirming candle should qualify an order block")
	}
}
