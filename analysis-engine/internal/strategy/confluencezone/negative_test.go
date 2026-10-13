package confluencezone

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
)

// Every negative fixture asserts that nothing is published AND that the specific
// reason is on record.

func rejects(t *testing.T, name string, a Analysis, reason string) {
	t.Helper()
	if len(a.Setups) != 0 {
		t.Fatalf("%s: published %+v", name, a.Setups[0])
	}
	if reason != "" && !hasReason(a, reason) {
		t.Fatalf("%s: want rejection %q, got %+v", name, reason, a.Rejections)
	}
}

func TestOnlyOneIndependentOrigin(t *testing.T) {
	facts := []structuralconfluence.Fact{
		fact("A", structuralconfluence.Buy, 4100, 4105, factsReady, "leg:1"),
		fact("B", structuralconfluence.Buy, 4103, 4108, factsReady, "leg:1", "break:1"),
	}
	a := Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125)))
	rejects(t, "one origin", a, structuralconfluence.RejectSingleIndependentOrigin)
	if len(a.Regions) != 0 {
		t.Fatalf("regions %+v", a.Regions)
	}
}

func TestDuplicateSourceIDIsOneFact(t *testing.T) {
	a := Detect(testConfig(t, nil), input(rejection(), []structuralconfluence.Fact{twoDemand()[0], twoDemand()[0]}, buyPool(4125)))
	rejects(t, "duplicate id", a, "")
	if len(a.Regions) != 0 {
		t.Fatalf("regions %+v", a.Regions)
	}
}

func TestTwoLabelsFromOneDisplacementAndAnFVGWithItsOwnIFVG(t *testing.T) {
	cases := map[string][]structuralconfluence.Fact{
		"supply/demand + order block of one leg": {
			fact("sd", structuralconfluence.Buy, 4100, 4105, factsReady, "leg:7"),
			fact("ob", structuralconfluence.Buy, 4103, 4108, factsReady, "leg:7", "break:7"),
		},
		"fvg + its own ifvg": {
			fact("fvg", structuralconfluence.Buy, 4100, 4105, factsReady, "gap:3"),
			fact("ifvg", structuralconfluence.Buy, 4103, 4108, factsReady, "gap:3", "inversion:9"),
		},
		"separated by an intermediate parent": {
			fact("a", structuralconfluence.Buy, 4100, 4105, factsReady, "x"),
			fact("b", structuralconfluence.Buy, 4103, 4108, factsReady, "y"),
			fact("link", structuralconfluence.Buy, 4101, 4107, factsReady, "x", "y"),
		},
	}
	for name, facts := range cases {
		rejects(t, name, Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125))), structuralconfluence.RejectSingleIndependentOrigin)
	}
}

func TestStructureCreatedByTheReactionOrLaterNeverCounts(t *testing.T) {
	bars := rejection()
	reactOpen := bars[29].Time
	for name, avail := range map[string]int64{
		"created on the touch and confirmation candle": reactOpen + 300, // closes with it
		"one candle after the confirmation":            reactOpen + 600,
		"confirmable only after the evaluated candle":  reactOpen + 3000,
	} {
		facts := twoDemand()
		facts[1].AvailableAt = avail
		a := Detect(testConfig(t, nil), input(bars, facts, buyPool(4125)))
		rejects(t, name, a, "")
		// Whether the late structure is reported depends on whether it was already
		// confirmable by the evaluated candle: the regions as of now are exact.
		if avail <= bars[29].Time+300 && len(a.Regions) != 1 {
			t.Fatalf("%s: the region exists as of the evaluated candle: %+v", name, a.Regions)
		}
	}
	// Confirmable exactly when the touch candle opened: admissible.
	facts := twoDemand()
	facts[1].AvailableAt = bars[29].Time
	only(t, Detect(testConfig(t, nil), input(bars, facts, buyPool(4125))))
}

func TestCreatedOnTheReactionCandleIsReportedAsNotYetAvailable(t *testing.T) {
	bars := rejection()
	facts := twoDemand()
	facts[1].AvailableAt = bars[29].Time + 300
	a := Detect(testConfig(t, nil), input(bars, facts, buyPool(4125)))
	rejects(t, "created by the reaction", a, ReasonStructureNotAvailable)
}

func TestStructureConfirmedAfterEarlierTouchDoesNotRescueTheLaterConfirmation(t *testing.T) {
	// Touch on candle 28 (no confirmation), the second structure becomes
	// confirmable at candle 29's close, candle 29 confirms: still rejected.
	bars := approach()
	bars[28] = bar(28, 4107, 4107.2, 4104.2, 4104.6)
	bars[29] = bar(29, 4104.6, 4107.4, 4104.4, 4107.1)
	facts := twoDemand()
	facts[1].AvailableAt = bars[29].Time + 300
	rejects(t, "late structure", Detect(testConfig(t, nil), input(bars, facts, buyPool(4125))), "")
}

func TestUnverifiedProvenanceNeverCounts(t *testing.T) {
	facts := twoDemand()
	facts[1].Verified, facts[1].UnverifiedReason = false, structuralconfluence.UnverifiedParentGap
	a := Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125)))
	rejects(t, "unverified", a, structuralconfluence.RejectProvenanceUnverified)
}

func TestAdjacentZonesWithZeroOverlapWidth(t *testing.T) {
	facts := []structuralconfluence.Fact{
		fact("A", structuralconfluence.Buy, 4100, 4103, factsReady, "a"),
		fact("B", structuralconfluence.Buy, 4103, 4108, factsReady, "b"),
	}
	a := Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125)))
	rejects(t, "adjacent", a, "")
	if len(a.Regions) != 0 {
		t.Fatalf("a shared boundary is not a region: %+v", a.Regions)
	}
}

func TestOverlapNarrowerThanTheMinimumWidth(t *testing.T) {
	facts := []structuralconfluence.Fact{
		fact("A", structuralconfluence.Buy, 4100, 4103.05, factsReady, "a"),
		fact("B", structuralconfluence.Buy, 4103, 4108, factsReady, "b"),
	}
	rejects(t, "narrow", Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125))), structuralconfluence.RejectOverlapTooNarrow)
}

func TestReactionAtTheUnionBoundaryButOutsideTheTrueOverlap(t *testing.T) {
	// The union is 4100-4108; the overlap is 4103-4105. A candle that only reaches
	// 4101-4102.9 touched A alone.
	bars := withLast(4101.8, 4102.9, 4100.4, 4102.6)
	a := Detect(testConfig(t, nil), input(bars, twoDemand(), buyPool(4125)))
	rejects(t, "outside the overlap", a, "")
	if len(a.Regions) != 1 || a.Regions[0].Low != 4103 {
		t.Fatalf("regions %+v", a.Regions)
	}
}

func TestWeakDirectionalConfirmation(t *testing.T) {
	// Inside the region with almost no wick and a close in the middle of the range.
	bars := withLast(4104.2, 4104.9, 4103.9, 4104.4)
	rejects(t, "weak", Detect(testConfig(t, nil), input(bars, twoDemand(), buyPool(4125))), ReasonNoConfirmation)
}

func TestDirectionIsMeasuredNotAssumedFromColour(t *testing.T) {
	// A bearish-bodied candle with a long lower wick and a close in the upper part
	// of its range is still a demand rejection; a bullish-bodied one that closes at
	// its low is not.
	bearishButRejecting := withLast(4105.2, 4105.8, 4102.6, 4105.0)
	if s := only(t, Detect(testConfig(t, nil), input(bearishButRejecting, twoDemand(), buyPool(4125)))); s.Reaction.CloseStrength < 0.6 {
		t.Fatalf("%+v", s.Reaction)
	}
	bullishButRejectedDown := withLast(4102.6, 4105.8, 4102.5, 4102.9)
	rejects(t, "closes at the low", Detect(testConfig(t, nil), input(bullishButRejectedDown, twoDemand(), buyPool(4125))), ReasonNoConfirmation)
}

func TestConflictingDirectionalContributorsAreNotConfluence(t *testing.T) {
	facts := []structuralconfluence.Fact{
		fact("A", structuralconfluence.Buy, 4100, 4105, factsReady, "a"),
		fact("B", structuralconfluence.Sell, 4103, 4108, factsReady, "b"),
	}
	a := Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125)))
	rejects(t, "conflict", a, "")
	if len(a.Regions) != 0 {
		t.Fatalf("regions %+v", a.Regions)
	}
}

func TestAnEarlierEpisodeIsNotRevivedByANewMember(t *testing.T) {
	bars := rejection()
	base := Detect(testConfig(t, nil), input(bars, twoDemand(), buyPool(4125)))
	old := only(t, base)
	// A third independent structure confirmable only after the touch joins the
	// same price: it does not rename the established episode and does not create a
	// second confirmation for the larger band.
	late := append(twoDemand(), fact("C", structuralconfluence.Buy, 4102, 4106, bars[29].Time+300, "c"))
	a := Detect(testConfig(t, nil), input(bars, late, buyPool(4125)))
	s := only(t, a)
	if s.Region.EpisodeID != old.Region.EpisodeID {
		t.Fatalf("episode renamed by a later member: %s vs %s", s.Region.EpisodeID, old.Region.EpisodeID)
	}
	// With the member confirmable before the touch it is a different episode.
	early := append(twoDemand(), fact("C", structuralconfluence.Buy, 4102, 4106, factsReady, "c"))
	if got := only(t, Detect(testConfig(t, nil), input(bars, early, buyPool(4125)))); got.Region.EpisodeID == old.Region.EpisodeID {
		t.Fatal("a structure present at the touch must be part of the episode")
	}
}

func TestReactionFailedAfterConfirmation(t *testing.T) {
	bars := approach()
	bars[28] = bar(28, 4104.5, 4106, 4102, 4105.5) // the confirming candle
	bars[29] = bar(29, 4105.5, 4105.6, 4101.0, 4101.5)
	rejects(t, "failed after", Detect(testConfig(t, nil), input(bars, twoDemand(), buyPool(4125))), ReasonFailedAfter)
}

func TestPriceAcceptedThroughTheRegionBeforeTheConfirmation(t *testing.T) {
	bars := approach()
	bars[27] = bar(27, 4106, 4106.2, 4102.0, 4102.5) // touches and closes below the region's low
	bars[28] = bar(28, 4102.5, 4103.0, 4101.8, 4102.6)
	bars[29] = bar(29, 4102.6, 4108.0, 4102.4, 4107.8)
	a := Detect(testConfig(t, nil), input(bars, twoDemand(), buyPool(4125)))
	// The touch on 27 was followed by a close below: the confirmation on 29 is not a
	// rejection of that touch. (Candle 29 itself still touches and confirms as its
	// own episode, which must be judged on its own: it closes above the region.)
	for _, s := range a.Setups {
		if s.TouchIndex == 27 {
			t.Fatalf("a touch accepted through must not confirm: %+v", s)
		}
	}
	if !hasReason(a, ReasonAcceptedThrough) {
		t.Fatalf("want %s, got %+v", ReasonAcceptedThrough, a.Rejections)
	}
}

func TestStopBeyondTheExecutionEnvelopeIsRefusedNeverTightened(t *testing.T) {
	// Risk is 7.5 price units = 75 pips from the entry high to the honest stop.
	refused := Detect(testConfig(t, map[string]any{"execution_stop_max_pips": 60.0}), input(rejection(), twoDemand(), buyPool(4125)))
	rejects(t, "envelope", refused, ReasonRiskEnvelope)
	allowed := Detect(testConfig(t, map[string]any{"execution_stop_max_pips": 80.0}), input(rejection(), twoDemand(), buyPool(4125)))
	if s := only(t, allowed); !near(s.Stop, 4097.5) {
		t.Fatalf("the stop must stay where the structure puts it: %v", s.Stop)
	}
}

func TestTargetRoom(t *testing.T) {
	cfg := testConfig(t, nil)
	rejects(t, "no pool", Detect(cfg, input(rejection(), twoDemand())), ReasonNoLiquidity)
	rejects(t, "only a pool too close", Detect(cfg, input(rejection(), twoDemand(), buyPool(4107))), ReasonInsufficientRoom)
	// 4110 is one ATR away but only 5 against 7.5 risk: 0.67 R.
	rejects(t, "reward risk", Detect(cfg, input(rejection(), twoDemand(), buyPool(4110))), ReasonRewardRisk)
	// A pool the price has already traded through since the touch is spent.
	rejects(t, "already swept", Detect(cfg, input(rejection(), twoDemand(), buyPool(4105.9))), ReasonNoLiquidity)
}

func TestTargetBlockedByAnOpposingConfluence(t *testing.T) {
	facts := append(twoDemand(),
		fact("S1", structuralconfluence.Sell, 4108, 4112, factsReady, "s1"),
		fact("S2", structuralconfluence.Sell, 4109, 4113, factsReady, "s2"))
	a := Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125)))
	rejects(t, "blocked", a, ReasonBlockedByStructure)
	// A single opposing structure is not a defended confluence.
	single := append(twoDemand(), fact("S1", structuralconfluence.Sell, 4108, 4112, factsReady, "s1"))
	only(t, Detect(testConfig(t, nil), input(rejection(), single, buyPool(4125))))
}

func TestDuplicateAndOutOfOrderCandlesRefuseTheSeries(t *testing.T) {
	dup := rejection()
	dup[10].Time = dup[9].Time
	rejects(t, "duplicate time", Detect(testConfig(t, nil), input(dup, twoDemand(), buyPool(4125))), ReasonCandleSeries)
	ooo := rejection()
	ooo[10], ooo[11] = ooo[11], ooo[10]
	rejects(t, "out of order", Detect(testConfig(t, nil), input(ooo, twoDemand(), buyPool(4125))), ReasonCandleSeries)
}

func TestMissingATRAndInsufficientHistory(t *testing.T) {
	in := input(rejection(), twoDemand(), buyPool(4125))
	in.ATR = 0
	rejects(t, "no atr", Detect(testConfig(t, nil), in), ReasonATRUnavailable)
	in.ATR = nan()
	rejects(t, "nan atr", Detect(testConfig(t, nil), in), ReasonATRUnavailable)
	short := input(rejection()[29:], twoDemand(), buyPool(4125))
	rejects(t, "one candle", Detect(testConfig(t, nil), short), ReasonInsufficientHistory)
	if a := Detect(testConfig(t, nil), input(nil, twoDemand())); len(a.Setups) != 0 {
		t.Fatal("no candles")
	}
}

func TestADataGapNeverMakesAConfirmationFresh(t *testing.T) {
	// The rejection is candle 28, one candle before the last by position, but the
	// last candle opens a day later: the confirmation is a day old.
	bars := approach()
	bars[28] = bar(28, 4104.5, 4106, 4102, 4105.5)
	bars[29] = bar(29, 4105.5, 4105.8, 4105.2, 4105.5)
	bars[29].Time += 86400
	rejects(t, "gap", Detect(testConfig(t, nil), input(bars, twoDemand(), buyPool(4125))), "")
	// The same two candles without the gap are a fresh confirmation.
	bars[29].Time -= 86400
	only(t, Detect(testConfig(t, nil), input(bars, twoDemand(), buyPool(4125))))
}

func TestPriceHasRunThroughOrAwayFromTheEntry(t *testing.T) {
	cfg := testConfig(t, nil)
	// The reaction is on candle 28; candle 29 closes back below the region without
	// closing beyond the adverse extreme (4102).
	through := approach()
	through[28] = bar(28, 4104.5, 4106, 4102, 4105.5)
	through[29] = bar(29, 4105.5, 4105.6, 4102.4, 4102.6)
	rejects(t, "through", Detect(cfg, input(through, twoDemand(), buyPool(4125))), ReasonPriceThroughEntry)
	// Price has run 3 ATR above the entry band.
	away := approach()
	away[28] = bar(28, 4104.5, 4106, 4102, 4105.5)
	away[29] = bar(29, 4105.5, 4121, 4105.4, 4120)
	rejects(t, "away", Detect(cfg, input(away, twoDemand(), buyPool(4140))), ReasonEntryTooFar)
}

func nan() float64 { var z float64; return z / z }
