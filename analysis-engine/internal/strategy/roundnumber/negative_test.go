package roundnumber

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

func hasReason(a Analysis, reason string) bool {
	for _, r := range a.Rejections {
		if r.Reason == reason {
			return true
		}
	}
	return false
}

// stopRunPrefix writes the path up to and including the approach to the 4110 handle (the swing high 4115.0 included).
func stopRunPrefix(withSwing bool) *scribe.Scribe {
	s := scribe.New(fixtureHour-60*300, 4113)
	s.Filler(60, 4113)
	if withSwing {
		s.Bar(4114.0, 0.6, 0.3)
		s.Bar(4114.6, 0.4, 0.2) // swing high 4115.0
		s.Bar(4113.4, 0.3, 0.5)
		s.Bar(4112.2, 0.3, 0.5)
	} else {
		s.Bar(4112.8, 0.3, 0.3)
		s.Bar(4112.2, 0.3, 0.3)
		s.Bar(4112.0, 0.3, 0.3)
		s.Bar(4111.6, 0.3, 0.3)
	}
	s.Bar(4110.6, 0.3, 0.3)
	return s
}

func expect(t *testing.T, bars []market.Candle, cfg Config, reason string) Analysis {
	t.Helper()
	got := Detect(cfg, Input{M5: bars})
	if len(got.Setups) != 0 || !hasReason(got, reason) {
		t.Fatalf("want no setup and %q, got setups %d rejections %+v", reason, len(got.Setups), got.Rejections)
	}
	return got
}

func TestASweepDeeperThanTheCeilingIsAcceptanceNotAStopRun(t *testing.T) {
	s := stopRunPrefix(true)
	s.Bar(4110.9, 0.2, 5.6) // low 4105.0: 50 pips under the handle, above the 40 pip ceiling
	s.Bar(4111.5, 0.3, 0.2)
	s.Bar(4115.6, 0.2, 0.1)
	expect(t, s.Bars, testConfig(t, nil), strategyutil.LevelReasonSweepTooDeep)
}

func TestClosesBeyondTheHandleAreAcceptedNotSwept(t *testing.T) {
	s := stopRunPrefix(true)
	s.Bar(4109.6, 0.2, 0.9) // sweep candle closes below the handle
	s.Bar(4109.2, 0.3, 0.3) // second close below: acceptance
	s.Bar(4109.0, 0.3, 0.3)
	expect(t, s.Bars, testConfig(t, nil), strategyutil.LevelReasonAccepted)
}

func TestNoReclaimInsideTheWindowIsRefused(t *testing.T) {
	s := stopRunPrefix(true)
	s.Bar(4109.6, 0.2, 0.9)
	for i := 0; i < 5; i++ {
		s.Bar(4109.5, 0.3, 0.3)
	}
	// Acceptance is raised high enough that the window, not the acceptance rule, ends the episode.
	expect(t, s.Bars, testConfig(t, map[string]any{"acceptance_closes": 20.0}), strategyutil.LevelReasonReclaimExpired)
}

func TestNoSwingToBreakMeansNoStructureReference(t *testing.T) {
	s := stopRunPrefix(false)
	s.Bar(4110.9, 0.2, 1.6)
	for i := 0; i < 8; i++ {
		s.Bar(4111.0+0.1*float64(i%2), 0.3, 0.3)
	}
	expect(t, s.Bars, testConfig(t, nil), strategyutil.LevelReasonNoStructure)
}

func TestAShiftThatNeverComesExpires(t *testing.T) {
	s := stopRunPrefix(true)
	s.Bar(4110.9, 0.2, 1.6)
	for i := 0; i < 8; i++ {
		s.Bar(4111.0+0.1*float64(i%2), 0.3, 0.3)
	}
	expect(t, s.Bars, testConfig(t, nil), strategyutil.LevelReasonMSSExpired)
}

func TestABreakOfTheSwingWithoutBodyAndCloseStrengthIsNotAShift(t *testing.T) {
	s := stopRunPrefix(true)
	s.Bar(4110.9, 0.2, 1.6)
	s.Bar(4111.5, 0.3, 0.2)
	s.Bar(4115.3, 3.0, 0.1) // closes above 4115.0 but with a 3.0 upper wick: weak close
	for i := 0; i < 8; i++ {
		s.Bar(4114.0, 0.3, 0.3)
	}
	expect(t, s.Bars, testConfig(t, nil), strategyutil.LevelReasonMSSQuality)
}

func TestAStaleConfirmationIsNotPublished(t *testing.T) {
	bars, _ := bullishStopRun()
	s := scribe.New(bars[len(bars)-1].Time+300, bars[len(bars)-1].Close)
	s.Bars = append(s.Bars, bars...)
	for i := 0; i < 3; i++ { // three candles later: older than confirmation_max_age_bars
		s.Bar(4116.0+0.2*float64(i), 0.3, 0.3)
	}
	// The handle stays in reach (max_handle_distance_atr) so only the age decides.
	wide := map[string]any{"max_handle_distance_atr": 30.0}
	got := Detect(testConfig(t, wide), Input{M5: s.Bars})
	if len(got.Setups) != 0 {
		t.Fatalf("a confirmation 3 candles old must not publish: %+v", got.Setups)
	}
	wide["confirmation_max_age_bars"] = 3.0
	if len(Detect(testConfig(t, wide), Input{M5: s.Bars}).Setups) != 1 {
		t.Fatal("the same episode must publish while it is within the age bound")
	}
}

func TestThePositionThatLostTheHandleAfterConfirmationIsRefused(t *testing.T) {
	bars, _ := bullishStopRun()
	s := scribe.New(bars[len(bars)-1].Time+300, bars[len(bars)-1].Close)
	s.Bars = append(s.Bars, bars...)
	s.Bar(4109.6, 0.3, 0.4) // a close back under the handle
	expect(t, s.Bars, testConfig(t, nil), ReasonInvalidatedAfterConfirm)
}

func TestASecondEpisodeOnTheSameHandleAndSideTheSameDayIsLocked(t *testing.T) {
	first, _ := bullishStopRun()
	s := scribe.New(first[len(first)-1].Time+300, first[len(first)-1].Close)
	s.Bars = append(s.Bars, first...)
	s.Filler(20, 4113)
	s.Bar(4114.0, 0.6, 0.3)
	s.Bar(4114.6, 0.4, 0.2) // a new swing high
	s.Bar(4113.4, 0.3, 0.5)
	s.Bar(4112.2, 0.3, 0.5)
	s.Bar(4110.6, 0.3, 0.3)
	s.Bar(4110.9, 0.2, 1.6) // a second sweep of the same handle
	s.Bar(4111.5, 0.3, 0.2)
	s.Bar(4115.6, 0.2, 0.1)
	got := Detect(testConfig(t, nil), Input{M5: s.Bars})
	if len(got.Setups) != 0 || !hasReason(got, ReasonHandleLocked) {
		t.Fatalf("want the second episode locked, got setups %d rejections %+v", len(got.Setups), got.Rejections)
	}
}

func TestASecondRetestIsStale(t *testing.T) {
	s := scribe.New(fixtureHour-60*300, 4107)
	s.Filler(60, 4107)
	s.Bar(4109.0, 0.3, 0.3)
	s.Bar(4112.4, 0.2, 0.2) // break
	s.Bar(4112.8, 0.3, 0.3)
	s.Bar(4112.0, 0.3, 0.3)
	s.Bar(4111.2, 0.2, 0.4) // first touch of the band: bearish close, no rejection
	s.Bar(4111.0, 0.2, 0.3) // the next candle does not reject either
	s.Bar(4111.9, 0.2, 0.3)
	s.Bar(4111.2, 0.2, 0.4) // a second touch
	expect(t, s.Bars, testConfig(t, nil), strategyutil.LevelReasonStaleRetest)
}

func TestAnUnacceptedBreakFails(t *testing.T) {
	s := scribe.New(fixtureHour-60*300, 4107)
	s.Filler(60, 4107)
	s.Bar(4109.0, 0.3, 0.3)
	s.Bar(4112.4, 0.2, 0.2) // break
	s.Bar(4109.6, 0.3, 0.3) // falls straight back under the handle: no acceptance
	expect(t, s.Bars, testConfig(t, nil), strategyutil.LevelReasonBreakFailed)
}

func TestARetestThatNeverComesIsRefused(t *testing.T) {
	s := scribe.New(fixtureHour-60*300, 4107)
	s.Filler(60, 4107)
	s.Bar(4109.0, 0.3, 0.3)
	s.Bar(4112.4, 0.2, 0.2)
	s.Bar(4112.8, 0.3, 0.3)
	s.Bar(4113.0, 0.3, 0.3)
	for i := 0; i < 16; i++ {
		s.Bar(4114.0+0.2*float64(i%2), 0.3, 0.3)
	}
	expect(t, s.Bars, testConfig(t, map[string]any{"max_handle_distance_atr": 30.0}), strategyutil.LevelReasonNoRetest)
}

func TestTheInstrumentScopeAndTheConfluenceFloorAreEnforced(t *testing.T) {
	bars, _ := bullishStopRun()
	other := marketContext(bars, "up")
	other.Symbol = "EURUSD"
	if got := newStrategy(t, nil).Evaluate(other); len(got) != 0 {
		t.Fatalf("a strategy scoped to XAU published on EURUSD: %d", len(got))
	}
	analysis, candidates := newStrategy(t, nil).(*Strategy).Analyze(marketContext(bars, "down"))
	if len(candidates) != 0 || !hasReason(analysis, ReasonConfluenceBelowFloor) {
		t.Fatalf("against the bias the setup is below the confluence floor: %d candidates, %+v", len(candidates), analysis.Rejections)
	}
}

func TestAStopAboveTheInstrumentCapIsRefusedAndCounted(t *testing.T) {
	bars, _ := bullishStopRun()
	s := newStrategy(t, map[string]any{"execution_stop_max_pips": 20.0, "execution_stop_min_pips": 10.0}).(*Strategy)
	analysis, candidates := s.Analyze(marketContext(bars, "up"))
	if len(candidates) != 0 || !hasReason(analysis, ReasonRiskExceedsEnvelope) {
		t.Fatalf("a 30 pip stop against a 20 pip cap must be refused: %d, %+v", len(candidates), analysis.Rejections)
	}
	if n := s.TakeStopBudgetRefusals(); n != 1 {
		t.Fatalf("refusals counted = %d, want 1", n)
	}
}

func TestAStopBelowTheFloorIsPublishedAndMarked(t *testing.T) {
	bars, _ := bullishStopRun()
	got := newStrategy(t, nil).Evaluate(marketContext(bars, "up")) // 30 pips against the XAU 50-70 envelope
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	if !evidenceHas(got[0].Evidence, strategyutil.StopBudgetFloorEvidence) {
		t.Fatalf("evidence %v lacks %s", got[0].Evidence, strategyutil.StopBudgetFloorEvidence)
	}
	inside := newStrategy(t, map[string]any{"execution_stop_min_pips": 20.0}).Evaluate(marketContext(bars, "up"))
	if len(inside) != 1 || !evidenceHas(inside[0].Evidence, strategyutil.StopBudgetStructuralEvidence) {
		t.Fatalf("a stop inside the envelope must be marked structural: %+v", inside)
	}
}
