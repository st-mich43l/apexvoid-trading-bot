package breakretest

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// expectBuy asserts the single BUY episode ends exactly here and publishes
// nothing unless the state is CANDIDATE.
func expectBuy(t *testing.T, cfg Config, bars []market.Candle, state State, reason string) {
	t.Helper()
	a := Detect(cfg, bars)
	ep := onlyLevel(t, a)
	if ep.State != state || ep.Reason != reason {
		t.Fatalf("state = %s (%q), want %s (%q); episode %+v", ep.State, ep.Reason, state, reason, ep)
	}
	if state != StateCandidate && len(a.Setups) != 0 {
		t.Fatalf("a %s episode published %d setup(s)", state, len(a.Setups))
	}
}

// onlyLevel is the single BUY key-level episode (other references, such as a
// line through incidental pivots, are judged by their own tests).
func onlyLevel(t *testing.T, a Analysis) Episode {
	t.Helper()
	var found []Episode
	for _, ep := range a.Episodes {
		if ep.Direction == market.Buy && ep.Reference.Kind == KindKeyLevel {
			found = append(found, ep)
		}
	}
	if len(found) != 1 {
		t.Fatalf("BUY key-level episodes = %d, want 1: %+v", len(found), found)
	}
	return found[0]
}

func level(mutate func(*levelOptions)) levelOptions {
	o := defaultLevel()
	if mutate != nil {
		mutate(&o)
	}
	return o
}

func TestNegativeFixtures(t *testing.T) {
	cfg := testConfig()
	build := func(mutate func(*levelOptions)) []market.Candle {
		bars, _ := bullishLevel(level(mutate))
		return bars
	}
	appended := func(mutate func(*levelOptions), extra func(s *scribe)) []market.Candle {
		s, _ := levelScribe(level(mutate))
		extra(s)
		return s.bars
	}
	cases := []struct {
		name   string
		cfg    func(*Config)
		bars   []market.Candle
		state  State
		reason string
	}{
		{"a single close beyond the level is not an accepted break", nil,
			build(func(o *levelOptions) { o.acceptClose, o.fallBackClose, o.touchLow = 0, 4118.0, 0 }),
			StateFalseBreakout, ReasonBreakNotAccepted},
		{"a weak break (small body, poor close) is not accepted", nil,
			build(func(o *levelOptions) { o.breakClose, o.breakUpper, o.acceptClose = 4120.6, 4.0, 4121.2 }),
			StateFalseBreakout, ReasonBreakQuality},
		{"closing back below the level before any retest is a false breakout", nil,
			appended(func(o *levelOptions) { o.touchLow = 0 }, func(s *scribe) { s.bar(4117.9, 0.4, 0.4) }),
			StateFalseBreakout, ReasonReclaimedBeforeRetst},
		{"no retest yet: waiting", nil,
			appended(func(o *levelOptions) { o.touchLow = 0 }, func(s *scribe) { s.bar(4124, 0.4, 0.4).bar(4124.2, 0.4, 0.4) }),
			StateRetestWaiting, ""},
		{"the retest comes after the window: expired", nil,
			build(func(o *levelOptions) { o.waitBars, o.touchLow = 26, 0 }),
			StateExpired, ReasonRetestWindowExpired},
		{"touched but never rejected within the window: expired", nil,
			appended(func(o *levelOptions) { o.rejectClose = 0 }, func(s *scribe) {
				s.bar(4120.9, 0.2, 0.4).bar(4120.8, 0.2, 0.4).bar(4120.7, 0.2, 0.4).bar(4120.6, 0.2, 0.4)
			}),
			StateExpired, ReasonConfirmWindowExpired},
		{"the retest closes through the level: failed", nil,
			appended(func(o *levelOptions) { o.rejectClose = 0 }, func(s *scribe) { s.bar(4118.5, 0.1, 0.3) }),
			StateRetestFailed, ReasonRetestClosedThrough},
		{"the protected structure is lost before the confirmation", nil,
			appended(func(o *levelOptions) { o.touchLow, o.rejectClose = 0, 0 }, func(s *scribe) { s.bar(4121, 0.2, 6.6) }),
			StateStructureInvalided, ReasonProtectedLost},
		{"the honest stop is beyond the execution envelope", func(c *Config) { c.ExecutionStopMaxPips = 40 },
			build(nil), StateRetestConfirmed, ReasonRiskEnvelope},
		{"no opposing structure to target", nil,
			build(func(o *levelOptions) { o.noSpike = true }), StateRetestConfirmed, ReasonNoOpposingStructure},
		{"the nearest opposing structure leaves too little room", func(c *Config) { c.MinimumTargetRoomATR = 5 },
			build(nil), StateRetestConfirmed, ReasonInsufficientRoom},
		{"reward to risk below the minimum", func(c *Config) { c.MinimumRewardRisk = 3 },
			build(nil), StateRetestConfirmed, ReasonRewardRisk},
		{"a confirmation older than the freshness window is not published", nil,
			appended(nil, func(s *scribe) { s.bar(4122.8, 0.3, 0.3).bar(4123.0, 0.3, 0.3).bar(4123.2, 0.3, 0.3) }),
			StateRetestConfirmed, ReasonConfirmationStale},
		{"price went through the entry band after the confirmation", nil,
			appended(nil, func(s *scribe) { s.bar(4119.4, 0.3, 0.1) }),
			StateRetestConfirmed, ReasonPriceThroughEntry},
		{"price ran too far from the entry", nil,
			appended(nil, func(s *scribe) { s.bar(4128, 0.3, 0.1) }),
			StateRetestConfirmed, ReasonEntryTooFar},
		{"the stop traded after the confirmation", nil,
			appended(nil, func(s *scribe) { s.bar(4121, 0.2, 8) }),
			StateRetestFailed, ReasonInvalidatedAfter},
		{"the objective was already reached after the confirmation", nil,
			appended(nil, func(s *scribe) { s.bar(4124, 8.5, 0.3) }),
			StateRetestConfirmed, ReasonTargetReached},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			cc := cfg
			if c.cfg != nil {
				c.cfg(&cc)
			}
			expectBuy(t, cc, c.bars, c.state, c.reason)
		})
	}
}

// A single touch is not a level: with one resistance touch there is no
// reference, so there is no episode at all (nothing to break).
func TestASingleTouchIsNotAReference(t *testing.T) {
	bars, _ := bullishLevel(level(func(o *levelOptions) { o.secondTouch = false }))
	a := Detect(testConfig(), bars)
	for _, ep := range a.Episodes {
		if ep.Direction == market.Buy && ep.Reference.Kind == KindKeyLevel {
			t.Errorf("a one-touch level produced an episode: %+v", ep)
		}
	}
	if len(a.Setups) != 0 {
		t.Errorf("setups = %d, want 0", len(a.Setups))
	}
}

// The stale-retest defect of the old strategy: a break long past, followed much
// later by an unrelated rejection near the old level. There is no fresh episode.
func TestAnOldBreakDoesNotProduceAFreshRetest(t *testing.T) {
	bars, _ := bullishLevel(level(func(o *levelOptions) { o.waitBars = 70 }))
	a := Detect(testConfig(), bars)
	if len(a.Setups) != 0 {
		t.Fatalf("a retest 70 candles after the break published: %+v", a.Setups[0])
	}
	for _, ep := range a.Episodes {
		if ep.Direction == market.Buy && ep.Reference.Kind == KindKeyLevel {
			t.Errorf("a 70 candle old break is still an episode: %+v", ep)
		}
	}
}

// A candle of the acceptance run touching the level is not the retest: the
// retest must come strictly after the acceptance.
func TestTheAcceptanceCandleIsNotTheRetest(t *testing.T) {
	s, m := levelScribe(level(func(o *levelOptions) { o.touchLow = 0 }))
	s.bars[m.accept].Low = 4120.1
	s.bar(4123.6, 0.3, 0.1).bar(4124.0, 0.3, 0.1)
	a := Detect(testConfig(), s.bars)
	ep := onlyLevel(t, a)
	if ep.State != StateRetestWaiting || len(a.Setups) != 0 {
		t.Fatalf("state = %s (%s), setups %d; want RETEST_WAITING and none", ep.State, ep.Reason, len(a.Setups))
	}
}

// A feed gap ages a confirmation: freshness is measured in time, not in slice
// positions.
func TestAFeedGapAgesTheConfirmation(t *testing.T) {
	bars, _ := bullishLevel(defaultLevel())
	last := bars[len(bars)-1]
	gapped := append(append([]market.Candle(nil), bars...), market.Candle{Time: last.Time + 300 + 3000, Open: 4122.6, High: 4122.9, Low: 4122.4, Close: 4122.8})
	expectBuy(t, testConfig(), gapped, StateRetestConfirmed, ReasonConfirmationStale)
}

func TestMalformedCandlesNeverPublish(t *testing.T) {
	bars, _ := bullishLevel(defaultLevel())
	cfg := testConfig()
	for name, mutate := range map[string]func([]market.Candle){
		"NaN close":      func(b []market.Candle) { b[40].Close = nan() },
		"high below low": func(b []market.Candle) { b[40].High = b[40].Low - 1 },
		"duplicate time": func(b []market.Candle) { b[41].Time = b[40].Time },
		"time reversal":  func(b []market.Candle) { b[41].Time = b[39].Time - 300 },
	} {
		t.Run(name, func(t *testing.T) {
			bad := append([]market.Candle(nil), bars...)
			mutate(bad)
			a := Detect(cfg, bad)
			if len(a.Setups) != 0 || len(a.Episodes) != 0 {
				t.Fatalf("malformed series produced %d setups, %d episodes", len(a.Setups), len(a.Episodes))
			}
		})
	}
}

func TestTooLittleHistoryProducesNothing(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	if a := Detect(testConfig(), bars[m.brk-8:]); len(a.Setups) != 0 {
		t.Errorf("a series too short to hold the structure published %d setups", len(a.Setups))
	}
	if a := Detect(testConfig(), nil); len(a.Setups) != 0 || len(a.Episodes) != 0 {
		t.Errorf("empty series produced output")
	}
}

// shifted moves every candle from index i on later by seconds, leaving a feed gap.
func shifted(bars []market.Candle, i int, seconds int64) []market.Candle {
	out := append([]market.Candle(nil), bars...)
	for k := i; k < len(out); k++ {
		out[k].Time += seconds
	}
	return out
}

// Audit finding: the k accepted closes must be consecutive candles; a feed gap
// inside the run is not an acceptance.
func TestAcceptanceRunAcrossAFeedGapIsNotAccepted(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	expectBuy(t, testConfig(), shifted(bars, m.accept, 1500), StateFalseBreakout, ReasonBreakNotAccepted)
}

// Audit finding: trading below the protected structure after its pivot but
// before the retest loop starts (the base, the break run) already lost it.
func TestProtectedStructureTradedBeforeTheBreakIsLost(t *testing.T) {
	for _, which := range []string{"base", "break-1", "accept"} {
		t.Run(which, func(t *testing.T) {
			bars, m := bullishLevel(defaultLevel())
			bars = append([]market.Candle(nil), bars...)
			idx := map[string]int{"base": m.brk - 2, "break-1": m.brk - 1, "accept": m.accept}[which]
			bars[idx].Low = 4105
			expectBuy(t, testConfig(), bars, StateStructureInvalided, ReasonProtectedLost)
		})
	}
}

// Audit finding: the reference and its tolerance come from what was knowable
// BEFORE the break candle. The break candle's own range must not decide whether
// a level exists.
func TestBreakCandleRangeDoesNotDecideWhetherAReferenceExists(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	cfg := testConfig()
	pre, ok := newATRWindow(bars, cfg.ATRLength, cfg.ATRWindowBars).at(m.brk - 1)
	if !ok {
		t.Fatal("no ATR")
	}
	// The two touches are 0.10 apart: a tolerance of 0.09 cannot cluster them.
	cfg.LevelClusterPips, cfg.LevelClusterATR = 0.001, 0.09/pre
	for _, wick := range []float64{0.3, 2.0, 6.0} {
		wide := append([]market.Candle(nil), bars...)
		wide[m.brk].High = math.Max(wide[m.brk].Open, wide[m.brk].Close) + wick
		for _, ep := range Detect(cfg, wide).Episodes {
			if ep.Direction == market.Buy && ep.Reference.Kind == KindKeyLevel && ep.Reference.Touches == 2 {
				t.Fatalf("break-candle wick %.1f made a 0.10 spread cluster under a 0.09 tolerance: %+v", wick, ep.Reference)
			}
		}
	}
}

// Audit finding: a gap between the second anchor and the break distorts a
// per-candle line value; such a line is not a reference.
func TestLineAcrossAGapAfterTheSecondAnchorIsNotAReference(t *testing.T) {
	bars, m := bullishLine(0)
	a := Detect(testConfig(), shifted(bars, m.brk-2, 1800))
	for _, ep := range a.Episodes {
		if ep.Reference.Kind == KindTrendline {
			t.Fatalf("a line spanning a feed gap is a reference: %+v", ep.Reference)
		}
	}
}

// Audit finding: when the best-ranked reference fails the confluence floor a
// weaker sibling that passes it must still be published.
func TestAWeakerSiblingSurvivesWhenTheBestFailsTheFloor(t *testing.T) {
	mk := func(id string, confirmed int64) Episode {
		s := Setup{Direction: market.Buy, ConfirmedAt: confirmed, Reference: Reference{ID: id, Touches: 2}}
		return Episode{Direction: market.Buy, Reference: s.Reference, BreakStart: 1, State: StateCandidate, Setup: &s}
	}
	episodes := []Episode{mk("best", 200), mk("weaker", 100)}
	got := resolve(episodes, func(s Setup) bool { return s.Reference.ID != "best" })
	if len(got.Setups) != 1 || got.Setups[0].Reference.ID != "weaker" {
		t.Fatalf("setups = %+v", got.Setups)
	}
	for _, ep := range got.Episodes {
		if ep.Reference.ID == "best" && (ep.State != StateRetestConfirmed || ep.Reason != ReasonConfluenceFloor) {
			t.Errorf("best = %s/%s, want RETEST_CONFIRMED/%s", ep.State, ep.Reason, ReasonConfluenceFloor)
		}
	}
	got = resolve([]Episode{mk("best", 200), mk("weaker", 100)}, nil)
	if len(got.Setups) != 1 || got.Setups[0].Reference.ID != "best" {
		t.Fatalf("unfiltered: %+v", got.Setups)
	}
}
