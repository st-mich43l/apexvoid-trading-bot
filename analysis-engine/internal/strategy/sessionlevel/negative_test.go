package sessionlevel

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func analyseWith(cfg Config, bars []market.Candle) Analysis { return Detect(cfg, Input{M5: bars}) }

// refused asserts that nothing is published and that the named level's episodes of
// the model end exactly as expected.
func refused(t *testing.T, a Analysis, level, model string, state State, reason string) {
	t.Helper()
	if len(a.Setups) != 0 {
		t.Fatalf("a setup was published: %+v", a.Setups[0])
	}
	for _, ep := range episodesOf(a, level, model) {
		if ep.State == state && ep.Reason == reason {
			return
		}
	}
	t.Fatalf("no %s/%s episode ended %s %q: %+v", level, model, state, reason, episodesOf(a, level, model))
}

func published(t *testing.T, a Analysis, model string) Setup {
	t.Helper()
	if len(a.Setups) != 1 || a.Setups[0].Reaction.Model != model {
		t.Fatalf("want one %s setup, got %d: %+v", model, len(a.Setups), a.Episodes)
	}
	return a.Setups[0]
}

func TestNoFreshApproachIsNoFirstTouch(t *testing.T) {
	bars, _ := firstTouchDay(opts{dwell: true})
	refused(t, analyse(bars), "LONDON_L", ModelFirstTouch, StateExpired, ReasonNoApproach)
}

func TestTheConfirmationWindowIsBounded(t *testing.T) {
	// The candle at touch+2 is the last one that may confirm (window 3 = touch..touch+2).
	published(t, analyse(mustBars(firstTouchDay(opts{waitBars: 1}))), ModelFirstTouch)
	bars, _ := firstTouchDay(opts{waitBars: 3})
	refused(t, analyse(bars), "LONDON_L", ModelFirstTouch, StateExpired, ReasonConfirmExpired)
}

func mustBars(bars []market.Candle, _ marks) []market.Candle { return bars }

func TestAWeakCloseIsNotARejection(t *testing.T) {
	// Closes above the level but in the lower half of its range, with a short wick
	// and a small body.
	bars, _ := firstTouchDay(opts{})
	last := &bars[len(bars)-1]
	last.Open, last.High, last.Low, last.Close = 4104.5, 4106.0, 4104.1, 4104.8
	a := analyse(bars)
	if len(a.Setups) != 0 {
		t.Fatalf("a weak candle confirmed a reaction: %+v", a.Setups[0])
	}
	if eps := episodesOf(a, "LONDON_L", ModelFirstTouch); len(eps) != 1 || eps[0].State != StateTouched {
		t.Fatalf("episodes = %+v", eps)
	}
	// The same level, with the candle a real wick rejection, is confirmed.
	last.High = 4104.9
	published(t, analyse(bars), ModelFirstTouch)
}

func TestACandleThatClosesBelowTheLevelIsNotARejection(t *testing.T) {
	bars, _ := firstTouchDay(opts{rejectClose: 4103.9, rejectOpen: 4103.2, rejectLow: 4103.1})
	a := analyse(bars)
	if len(a.Setups) != 0 {
		t.Fatalf("a close below the level confirmed a reaction: %+v", a.Setups[0])
	}
}

func TestTheSweepThresholdSeparatesAFirstTouchFromASweep(t *testing.T) {
	// 0.2 beyond the level is inside the 0.3 threshold: a (deep) first touch.
	s := published(t, analyse(mustBars(sweepDay(opts{sweepLow: 4103.8, sweepClose: 4103.9}))), ModelFirstTouch)
	if !(s.Reaction.PenetrationATR > 0) {
		t.Fatalf("penetration = %v", s.Reaction.PenetrationATR)
	}
	// 0.4 beyond it is a sweep.
	published(t, analyse(mustBars(sweepDay(opts{sweepLow: 4103.6, sweepClose: 4103.9}))), ModelSweepReclaim)
}

func TestASustainedBreakoutIsNotAReversal(t *testing.T) {
	// Two closes beyond the breakout buffer accept the break; a strong candle that
	// later closes back above the level is not a reclaim of anything.
	bars, _ := sweepDay(opts{reclaimGap: 2, gapClose: 4103.2})
	refused(t, analyse(bars), "LONDON_L", ModelSweepReclaim, StateBreakoutAccepted, ReasonBreakoutAccepted)
	// One such close is not yet an accepted break: the reclaim still stands.
	published(t, analyse(mustBars(sweepDay(opts{reclaimGap: 1, gapClose: 4103.2}))), ModelSweepReclaim)
}

func TestTheReclaimMustFollowTheSweepWithinTheWindow(t *testing.T) {
	// reclaim_max_bars = 6: the candle six periods after the sweep still reclaims it.
	published(t, analyse(mustBars(sweepDay(opts{reclaimGap: 5}))), ModelSweepReclaim)
	bars, _ := sweepDay(opts{reclaimGap: 6})
	refused(t, analyse(bars), "LONDON_L", ModelSweepReclaim, StateExpired, ReasonReclaimExpired)
}

func TestAShallowReclaimDoesNotReclaim(t *testing.T) {
	bars, _ := sweepDay(opts{reclaimClose: 4104.03})
	a := analyse(bars)
	if len(a.Setups) != 0 {
		t.Fatalf("a close inside the reclaim buffer reclaimed the level: %+v", a.Setups[0])
	}
	eps := episodesOf(a, "LONDON_L", ModelSweepReclaim)
	if len(eps) != 1 || (eps[0].State != StateSwept && eps[0].State != StateReclaimWaiting) {
		t.Fatalf("episodes = %+v", eps)
	}
}

func TestASweepThatIsNeverReclaimedIsNotAReaction(t *testing.T) {
	bars, _ := sweepDay(opts{noReclaim: true})
	a := analyse(bars)
	if len(a.Setups) != 0 {
		t.Fatalf("setups = %+v", a.Setups)
	}
}

func TestALevelThatWasAlreadySweptHasNoFirstTouch(t *testing.T) {
	bars, _ := firstTouchDay(opts{priorSweep: true})
	a := analyse(bars)
	if len(a.Setups) != 0 {
		t.Fatalf("a swept level published a first touch: %+v", a.Setups[0])
	}
	found := false
	for _, ep := range episodesOf(a, "LONDON_L", ModelFirstTouch) {
		if ep.State == StateExpired && ep.Reason == ReasonLevelSwept {
			found = true
		}
	}
	if !found {
		t.Fatalf("episodes = %+v", episodesOf(a, "LONDON_L", ModelFirstTouch))
	}
}

// The number of earlier touches is measured from the candles, never assumed.
func TestPriorTouchesAreCountedFromTheCandles(t *testing.T) {
	one := published(t, analyse(mustBars(firstTouchDay(opts{prior: 1}))), ModelFirstTouch)
	if !one.TouchesKnown || one.PriorTouches != 1 {
		t.Fatalf("touches = %d known=%v, want exactly the one earlier touch", one.PriorTouches, one.TouchesKnown)
	}
	bars, _ := firstTouchDay(opts{prior: 2})
	refused(t, analyse(bars), "LONDON_L", ModelFirstTouch, StateExpired, ReasonPriorTouches)
}

func TestTouchesBeforeAvailabilityAreNotTouches(t *testing.T) {
	bars, m := firstTouchDay(opts{})
	// A candle inside London re-tests the low while London is still forming.
	i := m.low + 20
	bars[i].Low = 4104.05
	bars[i].Open, bars[i].Close = 4108.4, 4108.5
	s := published(t, analyse(bars), ModelFirstTouch)
	if s.PriorTouches != 0 {
		t.Fatalf("a touch inside the defining period counted as a prior touch: %d", s.PriorTouches)
	}
}

func TestAnOlderConfirmationIsStale(t *testing.T) {
	published(t, analyse(mustBars(firstTouchDay(opts{afterBars: 2}))), ModelFirstTouch)
	bars, _ := firstTouchDay(opts{afterBars: 3})
	a := analyse(bars)
	refused(t, a, "LONDON_L", ModelFirstTouch, StateReactionConfirm, ReasonConfirmationStale)
}

func TestAReactionTheMarketHasAlreadyInvalidatedIsNotPublished(t *testing.T) {
	bars, m := firstTouchDay(opts{})
	extra := append(append([]market.Candle(nil), bars...), market.Candle{Time: bars[len(bars)-1].Time + 300, Open: 4105.5, High: 4105.6, Low: 4103.0, Close: 4103.4})
	_ = m
	a := analyse(extra)
	if len(a.Setups) != 0 {
		t.Fatalf("published after the stop traded: %+v", a.Setups[0])
	}
	refused(t, a, "LONDON_L", ModelFirstTouch, StateReactionFailed, ReasonInvalidatedAfter)
}

func TestATargetThatPriceHasAlreadyReachedIsNotPublished(t *testing.T) {
	bars, _ := firstTouchDay(opts{})
	last := bars[len(bars)-1]
	extra := append(append([]market.Candle(nil), bars...), market.Candle{Time: last.Time + 300, Open: 4106.0, High: 4119.0, Low: 4105.9, Close: 4118.9})
	refused(t, analyse(extra), "LONDON_L", ModelFirstTouch, StateReactionConfirm, ReasonTargetReached)
}

func TestPriceThatHasLeftTheEntryIsNotPublished(t *testing.T) {
	bars, _ := firstTouchDay(opts{})
	cfg := testConfig()
	cfg.MaximumEntryDistATR = 0.1
	refused(t, analyseWith(cfg, bars), "LONDON_L", ModelFirstTouch, StateReactionConfirm, ReasonEntryTooFar)
	last := bars[len(bars)-1]
	through := append(append([]market.Candle(nil), bars...), market.Candle{Time: last.Time + 300, Open: 4105.9, High: 4106.0, Low: 4103.5, Close: 4103.55})
	a := analyse(through)
	if len(a.Setups) != 0 {
		t.Fatalf("published a setup price had already traded through: %+v", a.Setups[0])
	}
}

func TestGeometryLimitsAreHonestAndNeverTightened(t *testing.T) {
	bars, _ := firstTouchDay(opts{})
	base := published(t, analyse(bars), ModelFirstTouch)

	cfg := testConfig()
	cfg.ExecutionStopMaxPips = base.RiskPips + 0.01
	published(t, analyseWith(cfg, bars), ModelFirstTouch)
	cfg.ExecutionStopMaxPips = base.RiskPips - 0.01
	refused(t, analyseWith(cfg, bars), "LONDON_L", ModelFirstTouch, StateReactionConfirm, ReasonRiskEnvelope)

	cfg = testConfig()
	cfg.MinimumTargetRoomATR = 100
	refused(t, analyseWith(cfg, bars), "LONDON_L", ModelFirstTouch, StateReactionConfirm, ReasonInsufficientRoom)
	cfg = testConfig()
	cfg.MinimumRewardRisk = 100
	refused(t, analyseWith(cfg, bars), "LONDON_L", ModelFirstTouch, StateReactionConfirm, ReasonRewardRisk)

	// A deep sweep: the stop goes beyond the whole sweep wick, and a cap that wick
	// breaks refuses the setup instead of tightening the stop inside it.
	deep, m := sweepDay(opts{sweepLow: 4101.0, sweepClose: 4103.9})
	s := published(t, analyse(deep), ModelSweepReclaim)
	if !(s.Stop < deep[m.sweep].Low) {
		t.Fatalf("stop %v is inside the sweep wick %v", s.Stop, deep[m.sweep].Low)
	}
	cfg = testConfig()
	cfg.ExecutionStopMaxPips = 30
	refused(t, analyseWith(cfg, deep), "LONDON_L", ModelSweepReclaim, StateReactionConfirm, ReasonRiskEnvelope)
}

func TestAsiaIsAFullLevelAndNotBlanketExcluded(t *testing.T) {
	bars, _ := firstTouchDay(opts{asia: true})
	s := published(t, analyse(bars), ModelFirstTouch)
	if s.Level.Name != "ASIA_L" || s.Level.Price != 4104.0 {
		t.Fatalf("level = %+v", s.Level)
	}
	if s.Level.PeriodEnd != dayAnchor+7*3600 {
		t.Fatalf("asia ended %d", s.Level.PeriodEnd)
	}
}

func TestNothingIsPublishedFromMalformedCandles(t *testing.T) {
	bars, _ := firstTouchDay(opts{})
	for name, mutate := range map[string]func([]market.Candle){
		"high below low":      func(b []market.Candle) { b[10].High = b[10].Low - 1 },
		"time goes backwards": func(b []market.Candle) { b[50].Time = b[40].Time },
		"a NaN":               func(b []market.Candle) { b[60].Close = nan() },
		"close outside range": func(b []market.Candle) { b[70].Close = b[70].High + 1 },
	} {
		t.Run(name, func(t *testing.T) {
			bad := append([]market.Candle(nil), bars...)
			mutate(bad)
			if a := analyse(bad); len(a.Setups) != 0 || len(a.Levels) != 0 {
				t.Fatalf("published from a malformed series: %+v", a)
			}
		})
	}
}
