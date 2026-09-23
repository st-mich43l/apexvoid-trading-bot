package zone

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func zoneTestConfig() Config {
	return Config{
		Displacement: DisplacementConfigForTest(),
		Lifecycle: LifecycleConfig{
			InvalidationToleranceATR: 0.5,
			SweepReclaimBars:         2,
			MaxBreakEpisodes:         2,
			RetestMaxTouches:         3,
			EpsilonATR:               0.05,
		},
		Relevance:              RelevanceConfig{ImmediateATR: 0.25, NearbyATR: 1.25, RemoteATR: 3.0},
		FlipAcceptBars:         2,
		FlipBandBodyFraction:   0.5,
		FlipLevelBandATR:       0.05,
		OrderBlockBodyFraction: 0.55,
	}
}

// DisplacementConfigForTest keeps the fixture explicit without coupling the
// test to any production config loader.
func DisplacementConfigForTest() structure.DisplacementConfig {
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
	state := Update(candles, []float64{1, 1, 1}, nil, nil, "M5", zoneTestConfig())

	if len(state.Zones) == 0 {
		t.Fatal("expected the three-candle sequence to produce a zone")
	}
	var found *Zone
	for i := range state.Zones {
		if state.Zones[i].Kind == KindFVG {
			found = &state.Zones[i]
			break
		}
	}
	if found == nil {
		t.Fatal("expected a demand FVG")
	}
	if found.Side != Demand || found.Low != 101 || found.High != 102 {
		t.Fatalf("unexpected FVG geometry: %+v", *found)
	}
	if found.State != StateFresh {
		t.Errorf("new FVG should be fresh, got %s", found.State)
	}
	if found.Relevance != Nearby {
		t.Errorf("FVG half an ATR away should be nearby, got %s", found.Relevance)
	}
}

func TestIFVGRequiresACloseThroughTheGapAndKeepsItsOwnIdentity(t *testing.T) {
	candles := []market.Candle{
		c(1, 100, 101, 99, 100),
		c(2, 100, 101, 99, 100),
		c(3, 101, 103, 102, 102.5),
		c(4, 102.5, 102.8, 99.5, 100),
		c(5, 100, 100.5, 98.5, 99),
	}
	state := Update(candles, []float64{1, 1, 1, 1, 1}, nil, nil, "M5", zoneTestConfig())

	var fvg, ifvg *Zone
	for i := range state.Zones {
		switch state.Zones[i].Kind {
		case KindFVG:
			if state.Zones[i].Low == 101 && state.Zones[i].High == 102 {
				fvg = &state.Zones[i]
			}
		case KindIFVG:
			if fvg != nil && state.Zones[i].DisplacementRef == fvg.ID {
				ifvg = &state.Zones[i]
			}
		}
	}
	if fvg != nil && ifvg == nil {
		for i := range state.Zones {
			if state.Zones[i].Kind == KindIFVG && state.Zones[i].DisplacementRef == fvg.ID {
				ifvg = &state.Zones[i]
				break
			}
		}
	}
	if fvg == nil || ifvg == nil {
		t.Fatalf("expected both FVG and iFVG, got %+v", state.Zones)
	}
	if ifvg.Side != Supply || ifvg.DisplacementRef != fvg.ID {
		t.Errorf("iFVG must invert the original and retain provenance: fvg=%+v ifvg=%+v", *fvg, *ifvg)
	}
}

func TestLifecycleForgivesReclaimedSweepButInvalidatesUnreclaimedBreak(t *testing.T) {
	cfg := zoneTestConfig().Lifecycle
	z := Zone{Side: Demand, Low: 100, High: 102}

	reclaimed := []market.Candle{
		c(1, 102, 102.5, 98.5, 99),
		c(2, 99, 101, 98.8, 100.2),
	}
	if !NotInvalidated(reclaimed, 0, z, 1, cfg) {
		t.Fatal("a breach reclaimed within the configured window must remain valid")
	}
	if got := DeriveState(reclaimed, 0, z, 1, 1, cfg); got != StateTouched {
		t.Fatalf("expected touched valid zone after reclaimed sweep, got %s", got)
	}

	unreclaimed := append(reclaimed[:1], c(2, 99, 99.5, 97, 98))
	if NotInvalidated(unreclaimed, 0, z, 1, cfg) {
		t.Fatal("an unreclaimed close beyond the far edge must invalidate")
	}
	if got := DeriveState(unreclaimed, 0, z, 1, 1, cfg); got != StateInvalidated {
		t.Fatalf("expected invalidated zone, got %s", got)
	}
}

func TestLifecycleSeparatesTouchExhaustionFromStructuralInvalidation(t *testing.T) {
	cfg := zoneTestConfig().Lifecycle
	z := Zone{Side: Supply, Low: 100, High: 102}
	valid := []market.Candle{c(1, 101, 102, 100, 101)}

	if got := DeriveState(valid, 0, z, 4, 1, cfg); got != StateMitigated {
		t.Fatalf("expected retest exhaustion to be mitigated, got %s", got)
	}
	if got := DeriveState(valid, 0, z, 2, 1, cfg); got != StatePartiallyMitigated {
		t.Fatalf("expected a still-valid multi-touch zone to be partially mitigated, got %s", got)
	}
}

func TestOrderBlockRequiresConfirmingBreakBodyQuality(t *testing.T) {
	weak := c(1, 100, 110, 99, 101)
	if qualifyingBreakBody(weak, 0.55) {
		t.Fatal("a wick-dominated confirming candle must not qualify an order block")
	}
	strong := c(1, 100, 102, 99, 101.8)
	if !qualifyingBreakBody(strong, 0.55) {
		t.Fatal("a body-dominant confirming candle should qualify an order block")
	}
}
