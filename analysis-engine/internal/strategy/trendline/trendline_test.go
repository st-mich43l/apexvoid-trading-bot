package trendline

import (
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	technical "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

func TestSetupTimeframesAndOverridesAreParsedFailClosed(t *testing.T) {
	frames, err := parseFrames(map[string]any{
		"setup_timeframes": []any{"M5", "M15"},
		"timeframes":       map[string]any{"M15": map[string]any{"maximum_bars_since_last_touch": 10, "expiry_hours": 24.0}},
	}, 30, 12, 3)
	if err != nil {
		t.Fatal(err)
	}
	if len(frames) != 2 || frames[0].timeframe != market.M5 || frames[1].timeframe != market.M15 {
		t.Fatalf("frames = %+v", frames)
	}
	if frames[0].maximumBarsSinceLastTouch != 30 || frames[0].expiryHours != 12 || frames[0].reactionLookbackBars != 3 {
		t.Errorf("M5 does not inherit the defaults: %+v", frames[0])
	}
	if frames[1].maximumBarsSinceLastTouch != 10 || frames[1].expiryHours != 24 || frames[1].reactionLookbackBars != 3 {
		t.Errorf("M15 override not applied: %+v", frames[1])
	}
	if frames[0].structureEvidence != "m5_trendline_causal_anchors" || frames[1].setupID != "m15_trendline_reaction_confirmed" {
		t.Errorf("evidence codes: %q %q", frames[0].structureEvidence, frames[1].setupID)
	}
	bad := map[string]map[string]any{
		"missing list":         {},
		"a timeframe above":    {"setup_timeframes": []any{"M5", "H1"}},
		"duplicate":            {"setup_timeframes": []any{"M5", "M5"}},
		"empty":                {"setup_timeframes": []any{}},
		"unknown override key": {"setup_timeframes": []any{"M5"}, "timeframes": map[string]any{"M5": map[string]any{"slope": 1.0}}},
		"override of a timeframe Trendline cannot decide on": {"setup_timeframes": []any{"M5"}, "timeframes": map[string]any{"H1": map[string]any{"expiry_hours": 1.0}}},
		"non-positive override":                              {"setup_timeframes": []any{"M5"}, "timeframes": map[string]any{"M5": map[string]any{"expiry_hours": 0.0}}},
	}
	for name, params := range bad {
		if _, err := parseFrames(params, 30, 12, 3); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	// M5 alone is a valid configuration (the strategy as it was before M15), and takes M15 off without removing its block.
	if frames, err := parseFrames(map[string]any{"setup_timeframes": []any{"M5"}, "timeframes": map[string]any{"M15": map[string]any{"expiry_hours": 24.0}}}, 30, 12, 3); err != nil || len(frames) != 1 {
		t.Errorf("M5 alone: %v %v", frames, err)
	}
}

func TestLineEligibility(t *testing.T) {
	s := &Strategy{minimumValidationTouches: 0, chopMinimumValidationTouches: 0, rejectExhausted: true, chopRequireHTFAligned: true}
	m5 := frame{timeframe: market.M5, maximumBarsSinceLastTouch: 30}
	m15 := frame{timeframe: market.M15, maximumBarsSinceLastTouch: 10}
	detector := func(kind string, aligned bool) *strategyutil.LegacyDetector {
		bias := "range"
		if aligned {
			bias = "up"
		}
		return &strategyutil.LegacyDetector{
			Frame: &analysiscontext.LegacyFrame{Regime: regime.State{Kind: kind}}, Read: analysiscontext.LegacyRead{HTFBias: bias}, Direction: market.Buy,
		}
	}
	validated := func(barIndex int) []technical.ValidationTouch {
		return []technical.ValidationTouch{{BarIndex: barIndex}}
	}
	brokenAt := int64(5)
	cases := []struct {
		name     string
		line     technical.Trendline
		d        *strategyutil.LegacyDetector
		f        frame
		rejected bool
	}{
		{"two anchors, no validation: eligible", technical.Trendline{State: technical.StateConfirmed}, detector("trend", false), m5, false},
		{"tentative (shorter than the minimum span): refused", technical.Trendline{State: technical.StateTentative}, detector("trend", true), m5, true},
		{"broken: refused", technical.Trendline{State: technical.StateBroken}, detector("trend", true), m5, true},
		{"unresolved close violation: refused", technical.Trendline{State: technical.StateConfirmed, BrokenAt: &brokenAt}, detector("trend", true), m5, true},
		{"degraded: refused", technical.Trendline{State: technical.StateDegraded}, detector("trend", true), m5, true},
		{"exhausted (worn through): refused", technical.Trendline{State: technical.StateExhausted, Exhausted: true}, detector("trend", true), m5, true},
		{"validated line touched 31 bars ago is stale on M5 (30)", technical.Trendline{State: technical.StateConfirmed, ValidationTouches: validated(69)}, detector("trend", true), m5, true},
		{"validated line touched 30 bars ago is not", technical.Trendline{State: technical.StateConfirmed, ValidationTouches: validated(70)}, detector("trend", true), m5, false},
		{"the stale bound is the frame's own: 11 bars is stale on a frame configured for 10", technical.Trendline{State: technical.StateConfirmed, ValidationTouches: validated(89)}, detector("trend", true), m15, true},
		{"chop without higher-timeframe alignment: refused", technical.Trendline{State: technical.StateConfirmed}, detector("chop", false), m5, true},
		{"chop with it: eligible (no extra touch required)", technical.Trendline{State: technical.StateConfirmed}, detector("chop", true), m5, false},
	}
	for _, c := range cases {
		if got := s.structurallyRejected(c.d, c.line, 100, c.f); got != c.rejected {
			t.Errorf("%s: rejected = %v, want %v", c.name, got, c.rejected)
		}
	}
}

func TestLineIdentityNamesTimeframeKindAndBothAnchors(t *testing.T) {
	bars := []market.Candle{{Time: 100}, {Time: 200}, {Time: 300}}
	line := technical.Trendline{Kind: technical.KindSupport, AnchorAIndex: 0, AnchorBIndex: 2}
	m5, m15 := lineID(market.M5, line, bars), lineID(market.M15, line, bars)
	if m5 != "trendline:M5:support:100:300" || m15 != "trendline:M15:support:100:300" || m5 == m15 {
		t.Errorf("ids %q %q", m5, m15)
	}
	// The window position of the anchors does not matter, only their times: the same line one bar later in a slid window keeps its id.
	slid := []market.Candle{{Time: 50}, {Time: 100}, {Time: 200}, {Time: 300}}
	if got := lineID(market.M5, technical.Trendline{Kind: technical.KindSupport, AnchorAIndex: 1, AnchorBIndex: 3}, slid); got != m5 {
		t.Errorf("slid window id %q, want %q", got, m5)
	}
	if !strings.Contains(lineID(market.M5, technical.Trendline{Kind: technical.KindResistance, AnchorAIndex: 0, AnchorBIndex: 2}, bars), ":resistance:") {
		t.Error("resistance id has no kind")
	}
}

// ---- TL-LIVE-2: single-price execution ----

func singlePriceStrategy() *Strategy {
	return &Strategy{singlePrice: true, proximityATR: 0.10, maxDriftATR: 0.25, validityBars: map[market.Timeframe]int{market.M5: 6, market.M15: 4}}
}

func linePrice(slope, intercept float64) technical.Trendline {
	return technical.Trendline{Kind: technical.KindSupport, Slope: slope, Intercept: intercept}
}

// The executable price is the line's projected value at the open of the bar AFTER the one that just closed: it uses closed bars only (the line's own anchors and
// the bars up to `last`), never a bar that has not happened.
func TestProjectionIsTheNextBarOfTheLineFromClosedBarsOnly(t *testing.T) {
	s := singlePriceStrategy()
	const step = 300
	bars := []market.Candle{{Time: 1000}, {Time: 1000 + step}, {Time: 1000 + 2*step}}
	last := 2
	// value(i) = 0.05*i + 4300: at the last closed bar 4300.10, at the next 4300.15.
	got, ok := s.project(frame{timeframe: market.M5, minutes: 5}, linePrice(0.05, 4300), bars, last, 2.0, 4299.7, 4300.5)
	if !ok {
		t.Fatal("a gently rising support must project")
	}
	if got.Price < 4300.1499 || got.Price > 4300.1501 {
		t.Fatalf("price %v, want the line at the next bar (4300.15)", got.Price)
	}
	if got.ProjectedAt != bars[last].Time+step {
		t.Fatalf("projected at %d, want the open of the bar after the observation (%d)", got.ProjectedAt, bars[last].Time+step)
	}
	if got.Timeframe != market.M5 || got.SlopePerBar < 0.0499 || got.SlopePerBar > 0.0501 || got.Proximity < 0.1999 || got.Proximity > 0.2001 {
		t.Fatalf("typed facts wrong: %+v", got)
	}
	// 0.25 ATR (0.50) over 0.05 per bar would allow 10 bars; the frame's own cap is 6.
	if want := got.ProjectedAt + 6*step; got.ValidUntil != want {
		t.Fatalf("valid until %d, want %d (6 bars, the frame cap)", got.ValidUntil, want)
	}
}

// A steeper line is valid for fewer bars: it stops being executable once it has moved max_drift_atr, and a line so steep that not even one bar fits has no
// one-price entry at all.
func TestValidityIsBoundedByHowFastTheLineMoves(t *testing.T) {
	s := singlePriceStrategy()
	bars := []market.Candle{{Time: 0}, {Time: 300}, {Time: 600}}
	// ATR 2.0, max drift 0.5. Slope 0.20/bar: floor(0.5/0.2) = 2 bars.
	got, ok := s.project(frame{timeframe: market.M5, minutes: 5}, linePrice(0.20, 4300), bars, 2, 2.0, 4299, 4302)
	if !ok || got.ValidUntil != got.ProjectedAt+2*300 {
		t.Fatalf("a 0.20/bar line is valid for 2 bars: %+v ok=%v", got, ok)
	}
	// Slope 0.80/bar moves 0.5 in under one bar: nothing is published.
	if _, ok := s.project(frame{timeframe: market.M5, minutes: 5}, linePrice(0.80, 4300), bars, 2, 2.0, 4299, 4305); ok {
		t.Fatal("a line that moves more than the drift bound in one bar must not carry a one-price entry")
	}
	// M15 has its own cap and its own bar length.
	m15, ok := s.project(frame{timeframe: market.M15, minutes: 15}, linePrice(0, 4300), bars, 2, 2.0, 4299, 4301)
	if !ok || m15.ValidUntil != m15.ProjectedAt+4*900 || m15.ProjectedAt != bars[2].Time+900 {
		t.Fatalf("M15 flat line: 4 bars of 15 minutes: %+v", m15)
	}
}

// The projection must stay inside the interaction corridor: a line whose next-bar value has left the corridor is not a price this setup was detected at.
func TestProjectionOutsideTheCorridorIsRefused(t *testing.T) {
	s := singlePriceStrategy()
	bars := []market.Candle{{Time: 0}, {Time: 300}, {Time: 600}}
	if _, ok := s.project(frame{timeframe: market.M5, minutes: 5}, linePrice(0.05, 4300), bars, 2, 2.0, 4299.0, 4300.10); ok {
		t.Fatal("4300.15 is above a corridor that ends at 4300.10")
	}
	if _, ok := s.project(frame{timeframe: market.M5, minutes: 5}, linePrice(0.05, 4300), bars, 2, 0, 4299.0, 4301); ok {
		t.Fatal("no ATR, no bounded validity: refused")
	}
}

func TestSinglePriceConfigurationIsRequiredAndFailClosed(t *testing.T) {
	s := &Strategy{frames: []frame{{timeframe: market.M5, minutes: 5}, {timeframe: market.M15, minutes: 15}}}
	good := func() map[string]any {
		return map[string]any{
			"single_price_execution": true, "single_price_proximity_atr": 0.1, "single_price_max_drift_atr": 0.25,
			"single_price_validity_bars": map[string]any{"M5": 6, "M15": 4},
		}
	}
	if err := s.parseSinglePrice(good()); err != nil || !s.singlePrice || s.validityBars[market.M15] != 4 {
		t.Fatalf("good configuration rejected: %v %+v", err, s)
	}
	for name, mutate := range map[string]func(map[string]any){
		"missing switch":     func(p map[string]any) { delete(p, "single_price_execution") },
		"non-boolean switch": func(p map[string]any) { p["single_price_execution"] = "yes" },
		"no proximity":       func(p map[string]any) { delete(p, "single_price_proximity_atr") },
		"zero drift":         func(p map[string]any) { p["single_price_max_drift_atr"] = 0.0 },
		"no validity map":    func(p map[string]any) { delete(p, "single_price_validity_bars") },
		"a frame missing":    func(p map[string]any) { p["single_price_validity_bars"] = map[string]any{"M5": 6} },
		"zero bars":          func(p map[string]any) { p["single_price_validity_bars"] = map[string]any{"M5": 0, "M15": 4} },
	} {
		p := good()
		mutate(p)
		if err := (&Strategy{frames: s.frames}).parseSinglePrice(p); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}

// A line that broke or stopped being respected is terminal; a standing line is live; a line the frame no longer holds is neither (expired).
func TestLineVerdictsFeedTheOpportunityLifecycle(t *testing.T) {
	bars := []market.Candle{{Time: 100}, {Time: 200}, {Time: 300}, {Time: 400}}
	brokenAt := int64(400)
	lines := []technical.Trendline{
		{Kind: technical.KindSupport, AnchorAIndex: 0, AnchorBIndex: 1, State: technical.StateConfirmed},
		{Kind: technical.KindSupport, AnchorAIndex: 0, AnchorBIndex: 2, State: technical.StateConfirmed, BrokenAt: &brokenAt},
		{Kind: technical.KindResistance, AnchorAIndex: 1, AnchorBIndex: 3, State: technical.StateDegraded},
	}
	var ledger strategyutil.ZoneLedger
	v := ledger.Begin()
	recordLines(v, market.M5, lines, bars)
	v.Publish()
	liveness, ok := ledger.Take()
	if !ok {
		t.Fatal("a frame was read: a verdict must be reported")
	}
	standing, broken, degraded := lineID(market.M5, lines[0], bars), lineID(market.M5, lines[1], bars), lineID(market.M5, lines[2], bars)
	if _, finished := liveness.Verdict(standing, market.M5); finished {
		t.Error("a standing line must stay live")
	}
	for _, id := range []string{broken, degraded} {
		if reason, finished := liveness.Verdict(id, market.M5); !finished || reason != "TRENDLINE_BROKEN" {
			t.Errorf("%s: %q %v, want TRENDLINE_BROKEN", id, reason, finished)
		}
	}
	if reason, finished := liveness.Verdict("trendline:M5:support:1:2", market.M5); !finished || reason != "ZONE_EXPIRED" {
		t.Errorf("a line the frame no longer holds must read expired: %q %v", reason, finished)
	}
	if _, finished := liveness.Verdict(broken, market.M15); finished {
		t.Error("the M15 frame was not read: M5's verdict says nothing about it")
	}
}
