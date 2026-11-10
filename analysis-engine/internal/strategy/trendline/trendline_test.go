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
