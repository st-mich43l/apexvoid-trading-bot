package shockretest

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

func evidenceHas(list []opportunity.Evidence, code string) bool {
	for _, e := range list {
		if e.Code == code {
			return true
		}
	}
	return false
}

// shockPrefix writes the quiet hour, the shock and its settle candle.
func shockPrefix() *scribe.Scribe {
	s := scribe.New(fixtureHour-60*300, 4100)
	s.Filler(60, 4100)
	s.Bar(4110.0, 0.3, 0.3)
	s.Bar(4109.5, 0.3, 0.3)
	return s
}

func expect(t *testing.T, bars []market.Candle, reason string) {
	t.Helper()
	got := Detect(testConfig(t, workable), Input{M5: bars})
	if len(got.Setups) != 0 || !hasReason(got, reason) {
		t.Fatalf("want no setup and %q, got setups %d rejections %+v", reason, len(got.Setups), got.Rejections)
	}
}

func TestNoPullbackIntoTheBandInTimeIsRefused(t *testing.T) {
	s := shockPrefix()
	for i := 0; i < 28; i++ {
		s.Bar(4108.0+0.3*float64(i%2), 0.3, 0.3) // hovers above the band
	}
	expect(t, s.Bars, ReasonNoRetest)
}

func TestAPullbackThatBecomesAReversalIsRefused(t *testing.T) {
	s := shockPrefix()
	s.Bar(4106.5, 0.2, 0.3)
	s.Bar(4101.0, 0.2, 0.3) // closes beyond the 78.6 percent retracement
	expect(t, s.Bars, ReasonRetestTooDeep)
}

func TestAPullbackIntoTheBandThatDoesNotRejectIsRefused(t *testing.T) {
	s := shockPrefix()
	s.Bar(4106.5, 0.2, 0.3)
	s.Bar(4105.0, 0.2, 0.3) // enters the band, closes down with no wick
	s.Bar(4104.8, 0.2, 0.3) // the next candle does not reject either
	expect(t, s.Bars, ReasonRetestNotRejected)
}

func TestAShockInTheOppositeDirectionEndsTheEpisode(t *testing.T) {
	s := shockPrefix()
	s.Bar(4108.8, 0.3, 0.3)
	s.Bar(4098.0, 0.3, 0.3) // a decisive bearish shock before the confirmation
	expect(t, s.Bars, ReasonCounterShock)
}

func TestAConfirmationThatPriceHasAlreadyLeftIsRefused(t *testing.T) {
	bars, _ := bullishShockRetest()
	s := scribe.New(bars[len(bars)-1].Time+300, bars[len(bars)-1].Close)
	s.Bars = append(s.Bars, bars...)
	s.Bar(4103.6, 0.2, 0.5) // back under the band, above the origin
	expect(t, s.Bars, ReasonPriceThroughEntry)
	t2 := scribe.New(bars[len(bars)-1].Time+300, bars[len(bars)-1].Close)
	t2.Bars = append(t2.Bars, bars...)
	t2.Bar(4099.5, 0.2, 0.5) // a close under the origin
	expect(t, t2.Bars, ReasonInvalidatedAfterConfirm)
}

func TestAStaleConfirmationIsNotPublished(t *testing.T) {
	bars, _ := bullishShockRetest()
	s := scribe.New(bars[len(bars)-1].Time+300, bars[len(bars)-1].Close)
	s.Bars = append(s.Bars, bars...)
	for i := 0; i < 3; i++ {
		s.Bar(4107.0+0.2*float64(i), 0.3, 0.3)
	}
	if got := Detect(testConfig(t, workable), Input{M5: s.Bars}); len(got.Setups) != 0 {
		t.Fatalf("a confirmation 3 candles old must not publish: %+v", got.Setups)
	}
	if got := Detect(testConfig(t, map[string]any{"minimum_target_r": 0.8, "confirmation_max_age_bars": 3.0}), Input{M5: s.Bars}); len(got.Setups) != 1 {
		t.Fatalf("within the age bound it must publish: %+v", got)
	}
}

func TestTheTargetAlreadyReachedIsRefused(t *testing.T) {
	bars, _ := bullishShockRetest()
	s := scribe.New(bars[len(bars)-1].Time+300, bars[len(bars)-1].Close)
	s.Bars = append(s.Bars, bars...)
	s.Bar(4111.0, 0.5, 0.2) // trades through the shock extreme straight after the confirmation
	expect(t, s.Bars, ReasonTargetAlreadyReached)
}

func TestAStopAboveTheInstrumentCapIsRefusedAndCounted(t *testing.T) {
	bars, _ := bullishShockRetest()
	s := newStrategy(t, map[string]any{"minimum_target_r": 0.8, "execution_stop_max_pips": 40.0, "execution_stop_min_pips": 10.0}).(*Strategy)
	analysis, candidates := s.Analyze(marketContext(bars, "up"))
	if len(candidates) != 0 || !hasReason(analysis, ReasonRiskExceedsEnvelope) {
		t.Fatalf("a 55 pip stop against a 40 pip cap must be refused: %d, %+v", len(candidates), analysis.Rejections)
	}
	if n := s.TakeStopBudgetRefusals(); n != 1 {
		t.Fatalf("refusals counted = %d, want 1", n)
	}
}

func TestAStopInsideTheEnvelopeIsMarkedStructuralAndBelowTheFloorMarkedFloor(t *testing.T) {
	bars, _ := bullishShockRetest()
	structural := newStrategy(t, workable).Evaluate(marketContext(bars, "up")) // 55 pips against the XAU 50-70 envelope
	if len(structural) != 1 || !evidenceHas(structural[0].Evidence, strategyutil.StopBudgetStructuralEvidence) {
		t.Fatalf("candidates %d: %+v", len(structural), structural)
	}
	floor := newStrategy(t, map[string]any{"minimum_target_r": 0.8, "execution_stop_min_pips": 80.0, "execution_stop_max_pips": 120.0}).Evaluate(marketContext(bars, "up"))
	if len(floor) != 1 || !evidenceHas(floor[0].Evidence, strategyutil.StopBudgetFloorEvidence) {
		t.Fatalf("a stop under the floor must be published marked: %+v", floor)
	}
}

func TestTheInstrumentScopeIsEnforced(t *testing.T) {
	bars, _ := bullishShockRetest()
	ctx := marketContext(bars, "up")
	ctx.Symbol = "EURUSD"
	if got := newStrategy(t, workable).Evaluate(ctx); len(got) != 0 {
		t.Fatalf("a strategy scoped to XAU published on EURUSD: %d", len(got))
	}
}
