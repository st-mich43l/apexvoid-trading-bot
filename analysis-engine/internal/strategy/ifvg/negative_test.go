package ifvg

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Every fixture here changes exactly one fact of the positive one (bullishIFVG with
// defaultOpts) and must publish nothing, for the stated reason.

// refused asserts the analysis published no setup and that the BUY gap episode
// ended in the given state with the given reason.
func refused(t *testing.T, name string, bars []market.Candle, cfg Config, state State, reason string) Analysis {
	t.Helper()
	a := Detect(cfg, bars)
	if len(a.Setups) != 0 {
		t.Fatalf("%s: published a setup; episodes: %s", name, describe(a))
	}
	if reason == "" {
		return a
	}
	ep, ok := episodeWith(a, market.Buy, reason)
	if !ok {
		t.Fatalf("%s: no BUY episode with reason %q; episodes: %s", name, reason, describe(a))
	}
	if ep.State != state {
		t.Fatalf("%s: episode state %s, want %s", name, ep.State, state)
	}
	return a
}

func TestAWickThroughTheGapIsNotAnInversion(t *testing.T) {
	// The inversion candle wicks far above the gap's top but closes inside it.
	o := defaultOpts()
	o.invertClose, o.invertUpper = 4115.0, 4.0
	bars, _ := bullishIFVG(o)
	a := Detect(testConfig(), bars)
	for _, ep := range a.Episodes {
		if ep.Direction == market.Buy && ep.InvertedAt != 0 {
			t.Fatalf("a wick through the gap created an inversion: %s", describe(a))
		}
	}
	if len(a.Setups) != 0 {
		t.Fatal("published on a wick-only break")
	}
}

func TestACloseJustBeyondTheGapInsideTheBufferIsNotInstitutionalDisplacement(t *testing.T) {
	o := defaultOpts()
	o.invertClose, o.invertUpper = 4116.05, 0.1 // 0.05 above the gap, buffer is 0.11+
	bars, _ := bullishIFVG(o)
	refused(t, "weak close", bars, testConfig(), StateInversionFailed, ReasonInversionBuffer)
}

func TestAWeakInversionCandleIsNotAnInversion(t *testing.T) {
	// Closes 1.6 above the gap but through a doji-like candle with a long upper wick.
	o := defaultOpts()
	o.invertClose, o.invertLow, o.invertUpper = 4117.6, 4111.0, 6.0
	bars, _ := bullishIFVG(o)
	refused(t, "weak body", bars, testConfig(), StateInversionFailed, ReasonInversionQuality)
}

func TestATinyGapIsNotAFairValueGap(t *testing.T) {
	o := defaultOpts()
	o.gapTop = 4112.5 // a 0.5 gap: under both the pip floor and 0.5 ATR
	bars, _ := bullishIFVG(o)
	a := Detect(testConfig(), bars)
	for _, ep := range a.Episodes {
		if ep.Direction == market.Buy {
			t.Fatalf("a sliver gap produced an episode: %s", describe(a))
		}
	}
	if len(a.Setups) != 0 {
		t.Fatalf("published on a tiny gap: %s", describe(a))
	}
}

func TestAWeakMiddleCandleIsNotADisplacementGap(t *testing.T) {
	o := defaultOpts()
	o.middleBody = 0.4 // far under 0.5 ATR
	bars, _ := bullishIFVG(o)
	if a := Detect(testConfig(), bars); len(a.Setups) != 0 {
		t.Fatalf("published without an origin displacement: %s", describe(a))
	}
}

func TestAGapInvertedTooLateIsStale(t *testing.T) {
	o := defaultOpts()
	o.baseBars = 80 // the base outlasts maximum_inversion_bars
	bars, _ := bullishIFVG(o)
	cfg := testConfig()
	cfg.TargetLookbackBars = 400 // the opposing swing is still in reach: only the gap's age refuses it
	cfg.EpisodeLookback = 200
	if a := Detect(cfg, bars); len(a.Setups) != 0 {
		t.Fatalf("published off a stale gap: %s", describe(a))
	}
}

func TestInversionAndRetestAreNotOneEvent(t *testing.T) {
	// The series ends on the inverting candle: it cannot also be its own retest.
	bars, m := bullishIFVG(defaultOpts())
	a := DetectAsOf(testConfig(), bars, m.invert)
	if len(a.Setups) != 0 {
		t.Fatalf("published on the inverting candle itself: %s", describe(a))
	}
}

func TestNoRetestMeansNoSetupAndTheWindowExpires(t *testing.T) {
	o := defaultOpts()
	o.touchLow = -1
	o.waitBars = 30 // price never comes back inside retest_max_bars
	bars, _ := bullishIFVG(o)
	refused(t, "no retest", bars, testConfig(), StateExpired, ReasonRetestWindowExpired)
}

func TestARetestWithoutARejectionExpires(t *testing.T) {
	o := defaultOpts()
	o.rejectClose = -1
	bars, m := bullishIFVG(o)
	// Quiet bars after the touch, none a rejection.
	s := &scribe{t: bars[len(bars)-1].Time + 300, last: bars[len(bars)-1].Close, bars: append([]market.Candle(nil), bars...)}
	_ = m
	for i := 0; i < 5; i++ {
		s.bar(4117.3-0.2*float64(i%2), 0.3, 0.3)
	}
	refused(t, "no rejection", s.bars, testConfig(), StateExpired, ReasonConfirmExpired)
}

func TestBearishCandleBelowTheGapTopIsNotARejection(t *testing.T) {
	o := defaultOpts()
	// A bullish candle that closes back INSIDE the gap (4115.6 < 4116): no rejection.
	o.rejectOpen, o.rejectLow, o.rejectClose = 4114.2, 4113.9, 4115.6
	bars, _ := bullishIFVG(o)
	if a := Detect(testConfig(), bars); len(a.Setups) != 0 {
		t.Fatalf("published on a close inside the gap: %s", describe(a))
	}
}

func TestACloseThroughTheWholeGapFailsTheRoleFlip(t *testing.T) {
	// After the inversion price falls back and CLOSES under the gap's bottom.
	bars, _ := bullishIFVG(defaultOpts())
	s := &scribe{t: bars[0].Time, last: 4110}
	s.bars = append([]market.Candle(nil), bars[:len(bars)-2]...)
	s.t = bars[len(bars)-2].Time
	s.last = bars[len(bars)-3].Close
	s.raw(4118.0, 4118.2, 4108.0, 4109.0) // a strong close under 4112 before any touch... it touches on the way
	a := Detect(testConfig(), s.bars)
	if len(a.Setups) != 0 {
		t.Fatalf("published after the role flip failed: %s", describe(a))
	}
	if _, ok := episodeWith(a, market.Buy, ReasonRoleFlipFailed); !ok {
		if _, ok2 := episodeWith(a, market.Buy, ReasonRetestClosedThrough); !ok2 {
			t.Fatalf("no failed-flip episode: %s", describe(a))
		}
	}
}

func TestStaleConfirmationIsNotPublished(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	s := &scribe{t: bars[len(bars)-1].Time + 300, last: bars[len(bars)-1].Close, bars: append([]market.Candle(nil), bars...)}
	for i := 0; i < 3; i++ { // confirmation_max_age_bars is 2
		s.bar(4118.4+0.1*float64(i%2), 0.3, 0.3)
	}
	refused(t, "stale", s.bars, testConfig(), StateRejectionConfirmed, ReasonConfirmationStale)
}

func TestPriceThroughTheEntryBandIsRefused(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	s := &scribe{t: bars[len(bars)-1].Time + 300, last: bars[len(bars)-1].Close, bars: append([]market.Candle(nil), bars...)}
	s.raw(4118.0, 4118.2, 4111.9, 4112.0-0.05) // a close under the entry band without hitting the stop
	a := Detect(testConfig(), s.bars)
	if len(a.Setups) != 0 {
		t.Fatalf("published with price through the entry: %s", describe(a))
	}
}

func TestPriceTooFarFromTheEntryIsRefused(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	s := &scribe{t: bars[len(bars)-1].Time + 300, last: bars[len(bars)-1].Close, bars: append([]market.Candle(nil), bars...)}
	s.raw(4118.2, 4128.5, 4118.0, 4128.0) // runs 11 away from the entry before the evaluated bar
	a := Detect(testConfig(), s.bars)
	if len(a.Setups) != 0 {
		t.Fatalf("published far from the entry: %s", describe(a))
	}
}

func TestTargetAlreadyReachedIsRefused(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	s := &scribe{t: bars[len(bars)-1].Time + 300, last: bars[len(bars)-1].Close, bars: append([]market.Candle(nil), bars...)}
	s.raw(4118.2, 4131.6, 4118.0, 4119.0)
	a := Detect(testConfig(), s.bars)
	if len(a.Setups) != 0 {
		t.Fatalf("published after the target was reached: %s", describe(a))
	}
	if _, ok := episodeWith(a, market.Buy, ReasonTargetReached); !ok {
		t.Fatalf("target_already_reached not recorded: %s", describe(a))
	}
}

func TestNoOpposingStructureMeansNoManufacturedTarget(t *testing.T) {
	o := defaultOpts()
	o.noSpike = true
	bars, _ := bullishIFVG(o)
	refused(t, "no target", bars, testConfig(), StateRejectionConfirmed, ReasonNoOpposingStructure)
}

func TestAnObstacleTooCloseIsInsufficientRoomNotASkippedBlocker(t *testing.T) {
	o := defaultOpts()
	o.spikeUpper = 9 // swing high ~4120: 3.5 above the entry, under 0.55 x 3.0 ATR... room 3.5 > 1.67
	bars, _ := bullishIFVG(o)
	cfg := testConfig()
	cfg.MinimumTargetRoomATR = 2.0 // the nearest credible swing leaves less than this
	refused(t, "room", bars, cfg, StateRejectionConfirmed, ReasonInsufficientRoom)
}

func TestARiskBeyondTheExecutionCapIsRefusedNeverTightened(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	free := candidateOf(t, Detect(testConfig(), bars))
	cfg := testConfig()
	cfg.ExecutionStopMaxPips = free.RiskPips - 1
	refused(t, "cap", bars, cfg, StateRejectionConfirmed, ReasonRiskEnvelope)
	cfg.ExecutionStopMaxPips = free.RiskPips + 1
	capped := candidateOf(t, Detect(cfg, bars))
	if capped.Stop != free.Stop {
		t.Fatalf("a cap above the risk must not move the stop: %.4f vs %.4f", capped.Stop, free.Stop)
	}
}

func TestRewardRiskBelowTheMinimumIsRefused(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	cfg := testConfig()
	cfg.MinimumRewardRisk = 99
	refused(t, "rr", bars, cfg, StateRejectionConfirmed, ReasonRewardRisk)
}

func TestMalformedSeriesPublishNothing(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	bad := append([]market.Candle(nil), bars...)
	bad[10].High = nan()
	if a := Detect(testConfig(), bad); len(a.Setups) != 0 || len(a.Episodes) != 0 {
		t.Fatal("a NaN candle must disable the evaluation")
	}
	unordered := append([]market.Candle(nil), bars...)
	unordered[20].Time = unordered[19].Time
	if a := Detect(testConfig(), unordered); len(a.Setups) != 0 {
		t.Fatal("non-increasing candle times must disable the evaluation")
	}
	inconsistent := append([]market.Candle(nil), bars...)
	inconsistent[30].High = inconsistent[30].Low - 1
	if a := Detect(testConfig(), inconsistent); len(a.Setups) != 0 {
		t.Fatal("high below low must disable the evaluation")
	}
}

func TestAFeedGapInsideTheFairValueGapBreaksItsConstruction(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	// Delete the middle candle B: A and C are no longer adjacent, so no gap exists.
	broken := append([]market.Candle(nil), bars[:m.b]...)
	broken = append(broken, bars[m.b+1:]...)
	a := Detect(testConfig(), broken)
	if len(a.Setups) != 0 {
		t.Fatalf("a gap was built across a missing candle: %s", describe(a))
	}
}

func TestRepeatedConfirmationOfTheSameTouchIsOneThesisAndOneCandidate(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	first := candidateOf(t, DetectAsOf(testConfig(), bars, m.confirm))
	s := &scribe{t: bars[len(bars)-1].Time + 300, last: bars[len(bars)-1].Close, bars: append([]market.Candle(nil), bars...)}
	s.bar(4118.5, 0.3, 0.3)
	again := candidateOf(t, Detect(testConfig(), s.bars))
	if again.ConfirmedAt != first.ConfirmedAt || again.Gap.ID != first.Gap.ID || again.Inversion.Time != first.Inversion.Time {
		t.Fatalf("the same touch produced a different setup: %+v vs %+v", first, again)
	}
}

func TestAnEarlierWeakCloseConsumesTheGapsInversion(t *testing.T) {
	// A close 0.05 beyond the gap is the gap's first close through it: it fails the
	// buffer, and a later strong candle is no longer its first.
	o := defaultOpts()
	o.earlyWeakClose = true
	bars, _ := bullishIFVG(o)
	refused(t, "early weak close", bars, testConfig(), StateInversionFailed, ReasonInversionBuffer)
}

func TestAFeedGapBetweenTheMiddleAndThirdCandleBreaksTheGap(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	if len(Detect(testConfig(), bars).Setups) != 1 {
		t.Fatal("the unshifted fixture must publish")
	}
	// An hour of missing candles between B and C: the three source candles are no
	// longer consecutive, so there is no gap, however clean the rest is.
	shifted := append([]market.Candle(nil), bars...)
	for i := m.c; i < len(shifted); i++ {
		shifted[i].Time += 3600
	}
	if a := Detect(testConfig(), shifted); len(a.Setups) != 0 {
		t.Fatalf("a gap was built across a feed hole: %s", describe(a))
	}
}

// retestAgain appends a departure (price leaves the gap by more than one ATR), a
// fresh touch of the gap's top and a rejection candle.
func retestAgain(bars []market.Candle, gapTop float64) []market.Candle {
	last := bars[len(bars)-1]
	s := &scribe{t: last.Time + 300, last: last.Close, bars: append([]market.Candle(nil), bars...)}
	s.bar(gapTop+3.6, 0.4, 0.2).bar(gapTop+5.2, 0.4, 0.2).bar(gapTop+4.6, 0.4, 0.4) // departure above the gap
	s.raw(gapTop+4.6, gapTop+4.8, gapTop+0.15, gapTop+1.4)                          // pullback touching the top
	s.raw(gapTop+0.8, gapTop+2.5, gapTop-0.6, gapTop+2.3)                           // rejection
	return s.bars
}

func TestASecondRetestAfterDepartureIsADistinctEpisodeOfTheSameThesis(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	first := candidateOf(t, Detect(testConfig(), bars))
	again := retestAgain(bars, 4116)
	a := Detect(testConfig(), again)
	second := candidateOf(t, a) // the first confirmation is old by now: only the fresh one is published
	if second.Gap.ID != first.Gap.ID || second.Inversion.Time != first.Inversion.Time {
		t.Fatalf("a second retest must belong to the same inverted gap: %+v vs %+v", second.Gap, first.Gap)
	}
	if second.ConfirmedAt <= first.ConfirmedAt || second.Retest.TouchTime <= first.ConfirmedAt || second.Retest.Touches != 2 {
		t.Fatalf("the second retest must come strictly after the first confirmation: %+v", second.Retest)
	}
	keys := map[string]bool{}
	for _, ep := range a.Episodes {
		if ep.Direction == market.Buy && ep.Setup != nil {
			keys[ep.Key()] = true
		}
	}
	if len(keys) != 1 {
		t.Fatalf("want one published episode: %s", describe(a))
	}
}

func TestATouchWithoutDepartureIsNotASecondEpisode(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	// Price hugs the gap's top (never a full ATR away) and touches it again.
	last := bars[len(bars)-1]
	s := &scribe{t: last.Time + 300, last: last.Close, bars: append([]market.Candle(nil), bars...)}
	s.bar(4118.6, 0.3, 0.3)
	s.raw(4118.4, 4118.6, 4116.1, 4117.5)
	s.raw(4116.9, 4118.7, 4115.5, 4118.6)
	a := Detect(testConfig(), s.bars)
	for _, ep := range a.Episodes {
		if ep.Direction == market.Buy && ep.Setup != nil && ep.ConfirmedAtOf() > bars[len(bars)-1].Time {
			t.Fatalf("a second episode appeared without a departure: %s", describe(a))
		}
	}
}

func TestAThirdRetestOfTheSameInversionIsNotEvaluated(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	third := retestAgain(retestAgain(bars, 4116), 4116)
	if a := Detect(testConfig(), third); len(a.Setups) != 0 {
		t.Fatalf("max_retests_per_inversion (2) was exceeded: %s", describe(a))
	}
}

// A gap exists once its third candle has closed, and only a LATER candle can invert it. The candles that formed the gap
// (A, B and C) never invert it, whatever they closed at: the formation of the gap is not the failure of the gap.
func TestTheFormationCandlesNeverInvertTheirOwnGap(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	atC := DetectAsOf(testConfig(), bars, m.c)
	for _, ep := range atC.Episodes {
		if ep.InvertedAt != 0 || ep.Gap.FormedAt == bars[m.c].Time && ep.State != StateFVGCreated && ep.State != StateInversionFailed {
			t.Fatalf("evaluated at the gap's third candle, an episode already moved past its formation: %+v", ep)
		}
	}
	full := Detect(testConfig(), bars)
	if len(full.Episodes) == 0 {
		t.Fatal("the fixture must invert its gap")
	}
	inverted := 0
	for _, ep := range full.Episodes {
		if ep.InvertedAt == 0 {
			continue
		}
		inverted++
		if ep.InvertedAt <= ep.Gap.FormedAt {
			t.Fatalf("the inversion (%d) must come after the gap formed (%d): %+v", ep.InvertedAt, ep.Gap.FormedAt, ep)
		}
		if ep.InvertedAt == bars[m.a].Time || ep.InvertedAt == bars[m.b].Time || ep.InvertedAt == bars[m.c].Time {
			t.Fatalf("a formation candle inverted its own gap: %+v", ep)
		}
	}
	if inverted == 0 {
		t.Fatal("no inversion recorded")
	}
}
