package asiarange

import (
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
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

func evidenceHas(list []opportunity.Evidence, code string) bool {
	for _, e := range list {
		if e.Code == code {
			return true
		}
	}
	return false
}

func TestARangeNarrowerOrWiderThanTheWindowIsNotATradableConsolidation(t *testing.T) {
	m5, m15, _ := bullishRaid()
	narrow := Detect(testConfig(t, map[string]any{"minimum_range_atr": 4.0, "maximum_range_atr": 8.0}), Input{M5: m5, M15: m15, Anchor: anchorInput()})
	if len(narrow.Setups) != 0 || !hasReason(narrow, ReasonRangeTooNarrow) {
		t.Fatalf("3.75 ATR against a 4 ATR minimum: %+v", narrow)
	}
	wide := Detect(testConfig(t, map[string]any{"minimum_range_atr": 0.5, "maximum_range_atr": 3.0}), Input{M5: m5, M15: m15, Anchor: anchorInput()})
	if len(wide.Setups) != 0 || !hasReason(wide, ReasonRangeTooWide) {
		t.Fatalf("3.75 ATR against a 3 ATR maximum: %+v", wide)
	}
}

func TestWithoutAFinalizedAnchorThereIsNoThesis(t *testing.T) {
	m5, m15, _ := bullishRaid()
	if got := Detect(testConfig(t, nil), Input{M5: m5, M15: m15}); len(got.Setups) != 0 || got.Anchored {
		t.Fatalf("no anchor must mean no setup: %+v", got)
	}
	// A level that becomes knowable only after the evaluated candle is not used.
	late := &RawAnchor{High: asiaHigh, Low: asiaLow, AvailableAt: m5[len(m5)-1].Time + 3600}
	if got := Detect(testConfig(t, nil), Input{M5: m5, M15: m15, Anchor: late}); got.Anchored || len(got.Setups) != 0 {
		t.Fatalf("a level used before its AvailableAt: %+v", got)
	}
}

func TestAnEpisodeMustStartInsideTheActiveWindow(t *testing.T) {
	m5, m15, _ := bullishRaid()
	// The sweep candle opens 30 minutes after London open: a window of 15 minutes excludes it.
	if got := Detect(testConfig(t, map[string]any{"active_window_hours": 0.25}), Input{M5: m5, M15: m15, Anchor: anchorInput()}); len(got.Setups) != 0 {
		t.Fatalf("an episode that started outside the window published: %+v", got.Setups)
	}
}

func TestAnExcursionThatIsNeverTakenBackExpires(t *testing.T) {
	_, m15, _ := bullishRaid()
	s := scribe.New(london-70*300, 4104)
	s.Filler(70, 4104)
	s.Bar(4105.2, 0.4, 0.3)
	s.Bar(4106.0, 0.4, 0.3)
	s.Bar(4105.0, 0.1, 0.4)
	s.Bar(4101.0, 0.3, 0.4)
	s.Bar(4099.6, 0.2, 1.4)  // closes under the edge
	for i := 0; i < 8; i++ { // longer than reclaim_max_bars: the market lives below the edge, it is not a stop run
		s.Bar(4099.2-0.1*float64(i%2), 0.3, 0.3)
	}
	got := Detect(testConfig(t, nil), Input{M5: s.Bars, M15: m15, Anchor: anchorInput()})
	if len(got.Setups) != 0 || !hasReason(got, strategyutil.LevelReasonReclaimExpired) {
		t.Fatalf("want reclaim_window_expired, got %+v", got)
	}
}

func TestATargetWithoutRoomIsRefused(t *testing.T) {
	m5, m15, _ := bullishRaid()
	// Even the opposite edge (30 units away) cannot give a 20R reward.
	got := Detect(testConfig(t, map[string]any{"minimum_target_r": 20.0}), Input{M5: m5, M15: m15, Anchor: anchorInput()})
	if len(got.Setups) != 0 || !hasReason(got, ReasonNoTargetRoom) {
		t.Fatalf("want no_target_room, got %+v", got)
	}
}

func TestTheMidIsSkippedWhenItGivesNoRoomButTheOppositeEdgeDoes(t *testing.T) {
	m5, m15, _ := bullishRaid()
	// Risk 29 pips: the mid (140 pips away) clears 4R but not 6R; the opposite edge (290 pips) clears 6R.
	got := Detect(testConfig(t, map[string]any{"minimum_target_r": 6.0}), Input{M5: m5, M15: m15, Anchor: anchorInput()})
	if len(got.Setups) != 1 || got.Setups[0].TargetKind != TargetOppositeEdge || got.Setups[0].Target != asiaHigh {
		t.Fatalf("want the opposite edge, got %+v rejections %+v", got.Setups, got.Rejections)
	}
}

func TestTheFirstConfirmedEpisodeOwnsTheEdgeAndALaterOneIsLocked(t *testing.T) {
	m5, m15, idx := bullishRaid()
	s := scribe.New(m5[len(m5)-1].Time+300, m5[len(m5)-1].Close)
	s.Bars = append(s.Bars, m5...)
	s.Filler(12, 4106)
	s.Bar(4107.4, 0.4, 0.3)
	s.Bar(4108.0, 0.4, 0.3) // a new swing high 4108.4
	s.Bar(4106.9, 0.1, 0.4)
	s.Bar(4105.0, 0.1, 0.4)
	s.Bar(4101.0, 0.3, 0.4)
	s.Bar(4101.4, 0.2, 2.4) // a second raid of the same low
	s.Bar(4102.0, 0.3, 0.2)
	s.Bar(4109.0, 0.2, 0.1)
	_ = idx
	got := Detect(testConfig(t, nil), Input{M5: s.Bars, M15: m15, Anchor: anchorInput()})
	if len(got.Setups) != 0 || !hasReason(got, ReasonEdgeLocked) {
		t.Fatalf("the second episode on the edge must be locked: setups %d rejections %+v", len(got.Setups), got.Rejections)
	}
}

func TestARaidOfOneEdgeDoesNotLockTheOther(t *testing.T) {
	m5, m15, _ := bullishRaid()
	low := Detect(testConfig(t, nil), Input{M5: m5, M15: m15, Anchor: anchorInput()}).Setups
	if len(low) != 1 || low[0].Edge != EdgeLow {
		t.Fatalf("setups %+v", low)
	}
	// The mirrored path raids the high; both edges exist in the same anchor and neither lock leaks across.
	high := Detect(testConfig(t, nil), Input{M5: scribe.Reflect(m5, asiaMid), M15: reflectM15(m15), Anchor: anchorInput()}).Setups
	if len(high) != 1 || high[0].Edge != EdgeHigh {
		t.Fatalf("setups %+v", high)
	}
}

func TestAcceptanceThatLosesTheEdgeFails(t *testing.T) {
	m5, m15, idx := bullishAcceptance()
	s := scribe.New(m5[idx.brk+1].Time, 4131.8)
	s.Bars = append(s.Bars, m5[:idx.brk+1]...)
	s.Bar(4129.4, 0.3, 0.3) // falls straight back under the edge: no acceptance
	got := Detect(testConfig(t, nil), Input{M5: s.Bars, M15: m15, Anchor: anchorInput()})
	if len(got.Setups) != 0 || !hasReason(got, strategyutil.LevelReasonBreakFailed) {
		t.Fatalf("want break_failed, got %+v", got)
	}
}

func TestAStopAboveTheInstrumentCapIsRefusedAndCounted(t *testing.T) {
	m5, m15, _ := bullishRaid()
	s := newStrategy(t, map[string]any{"execution_stop_max_pips": 20.0, "execution_stop_min_pips": 10.0}).(*Strategy)
	analysis, candidates := s.Analyze(marketContext(m5, m15, "up"))
	if len(candidates) != 0 || !hasReason(analysis, ReasonRiskExceedsEnvelope) {
		t.Fatalf("a 29 pip stop against a 20 pip cap must be refused: %d, %+v", len(candidates), analysis.Rejections)
	}
	if n := s.TakeStopBudgetRefusals(); n != 1 {
		t.Fatalf("refusals counted = %d, want 1", n)
	}
}

func TestAStopBelowTheFloorIsPublishedAndMarked(t *testing.T) {
	m5, m15, _ := bullishRaid()
	got := newStrategy(t, nil).Evaluate(marketContext(m5, m15, "up")) // 29 pips against the XAU 50-70 envelope
	if len(got) != 1 || !evidenceHas(got[0].Evidence, strategyutil.StopBudgetFloorEvidence) {
		t.Fatalf("candidates %d: %+v", len(got), got)
	}
	inside := newStrategy(t, map[string]any{"execution_stop_min_pips": 20.0}).Evaluate(marketContext(m5, m15, "up"))
	if len(inside) != 1 || !evidenceHas(inside[0].Evidence, strategyutil.StopBudgetStructuralEvidence) {
		t.Fatalf("a stop inside the envelope must be marked structural: %+v", inside)
	}
}

func TestTheInstrumentScopeIsEnforced(t *testing.T) {
	m5, m15, _ := bullishRaid()
	ctx := marketContext(m5, m15, "up")
	ctx.Symbol = "EURUSD"
	if got := newStrategy(t, nil).Evaluate(ctx); len(got) != 0 {
		t.Fatalf("a strategy scoped to XAU published on EURUSD: %d", len(got))
	}
}

// Bar-by-bar evaluation equals whole-window detection, and a decision at candle e never reads a later candle or an M15 candle not yet closed.
func TestAppendingFutureCandlesNeverChangesADecisionAlreadyMade(t *testing.T) {
	raid5, raid15, _ := bullishRaid()
	acc5, acc15, _ := bullishAcceptance()
	for name, in := range map[string]Input{
		"raid BUY":        {M5: raid5, M15: raid15, Anchor: anchorInput()},
		"raid SELL":       {M5: scribe.Reflect(raid5, asiaMid), M15: reflectM15(raid15), Anchor: anchorInput()},
		"acceptance BUY":  {M5: acc5, M15: acc15, Anchor: anchorInput()},
		"acceptance SELL": {M5: scribe.Reflect(acc5, asiaMid), M15: reflectM15(acc15), Anchor: anchorInput()},
	} {
		cfg := testConfig(t, nil)
		future := scribe.New(in.M5[len(in.M5)-1].Time+300, in.M5[len(in.M5)-1].Close)
		future.Bars = append(future.Bars, in.M5...)
		future.Bar(4090.0, 1, 1)
		future.Bar(4150.0, 1, 1)
		withFuture := Input{M5: future.Bars, M15: append(append([]market.Candle(nil), in.M15...), market.Candle{Time: ((in.M5[len(in.M5)-1].Time+300)/900 + 1) * 900, Open: 4100, High: 4200, Low: 4000, Close: 4180}), Anchor: in.Anchor}
		for asOf := 75; asOf < len(in.M5); asOf++ {
			got := DetectAsOf(cfg, withFuture, asOf)
			want := Detect(cfg, Input{M5: in.M5[:asOf+1], M15: in.M15, Anchor: in.Anchor})
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("%s at candle %d: the future changed the decision\n got  %+v\n want %+v", name, asOf, got, want)
			}
		}
	}
}

func TestBarByBarEvaluationPublishesEachThesisOnce(t *testing.T) {
	m5, m15, idx := bullishRaid()
	cfg := testConfig(t, nil)
	published := 0
	for asOf := 75; asOf < len(m5); asOf++ {
		got := Detect(cfg, Input{M5: m5[:asOf+1], M15: m15, Anchor: anchorInput()})
		if asOf < idx.shift && len(got.Setups) != 0 {
			t.Fatalf("a setup was published at candle %d, before its confirmation at %d", asOf, idx.shift)
		}
		published += len(got.Setups)
	}
	if published != 1 {
		t.Fatalf("published %d times, want once at the confirming candle", published)
	}
}
