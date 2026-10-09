package breakretest

import (
	"fmt"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// brParams is the strategy's own parameter set as config/analysis.yml declares
// it (XAU scale), merged with the shared detector contract the engine injects.
func brParams(override map[string]any) map[string]any {
	own := map[string]any{
		"atr_length": 14.0, "atr_window_bars": 140.0, "pivot_bars": 2.0, "reference_lookback_bars": 120.0, "episode_lookback_bars": 48.0,
		"level_min_touches": 2.0, "level_cluster_pips": 2.0, "level_cluster_atr": 0.4, "level_span_multiple": 2.0,
		"line_min_span_bars": 6.0, "line_min_slope_atr": 0.01, "line_max_slope_atr": 0.10, "line_wick_atr": 0.25, "line_max_gap_ratio": 1.5,
		"pre_break_bars": 3.0, "breakout_accept_bars": 2.0, "break_buffer_pips": 1.0, "break_buffer_atr": 0.05,
		"minimum_break_body_ratio": 0.5, "minimum_break_close_strength": 0.6, "minimum_break_displacement_atr": 0.5, "displacement_grade_atr": 1.0,
		"break_fail_pips": 2.0, "break_fail_atr": 0.30, "retest_max_bars": 24.0, "retest_touch_pips": 1.0, "retest_touch_atr": 0.15,
		"confirmation_window_bars": 3.0, "confirmation_max_age_bars": 2.0, "rejection_close_strength": 0.6, "rejection_wick_ratio": 0.25,
		"rejection_body_ratio": 0.6, "protected_structure": "last_pivot", "entry_tolerance_pips": 1.0, "entry_tolerance_atr": 0.15,
		"maximum_entry_distance_atr": 1.5, "invalidation_buffer_atr": 0.25, "target_lookback_bars": 96.0, "target_swing_bars": 12.0,
		"target_swing_atr": 1.0, "minimum_target_room_atr": 0.55, "minimum_reward_risk": 1.15, "execution_stop_max_pips": 120.0,
		"expiry_hours": 4.0, "published_overall_quality": 0.75,
	}
	for k, v := range override {
		own[k] = v
	}
	return legacyfixture.Params(own)
}

func newStrategy(t *testing.T, override map[string]any) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: brParams(override)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// marketContext carries the closed M5 candles, the engine's structural read of
// the M5 frame and an HTF bias, the way the engine hands them to a strategy.
func marketContext(m5 []market.Candle, structure, htf string) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU",
		Legacy: &analysiscontext.LegacyRead{LocalStructure: structure, HTFBias: htf, AllowCounterTrend: true},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5: {Timeframe: market.M5, Candles: m5, Legacy: &analysiscontext.LegacyFrame{Structure: structure}},
		},
	}
}

func TestCandidateCarriesTheTechnicalContractAndPassesValidation(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	got := newStrategy(t, nil).Evaluate(marketContext(bars, "up", "up"))
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	c := got[0]
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	if c.Strategy != ID || c.StrategyVersion != Version || c.Direction != market.Buy || c.Symbol != "XAU" {
		t.Fatalf("identity = %+v", c)
	}
	want := fmt.Sprintf("technique:break_retest:buy:level:%d:%d", bars[m.pivot1].Time, bars[m.brk].Time)
	if c.StructuralID != want {
		t.Fatalf("structural id = %q, want %q", c.StructuralID, want)
	}
	if c.FormedAt != bars[m.accept].Time || c.CreatedAt != bars[m.confirm].Time || c.ExpiresAt != c.CreatedAt+4*3600 {
		t.Fatalf("formed %d created %d expires %d", c.FormedAt, c.CreatedAt, c.ExpiresAt)
	}
	if c.Invalidation.Label != "break_retest_structure_lost" || !(float64(c.Invalidation.Price) < 4115) {
		t.Fatalf("invalidation = %+v", c.Invalidation)
	}
	if len(c.Targets) != 1 || float64(c.Targets[0].Price.Price) != 4132 || c.Targets[0].Price.Label != "opposing_structure" {
		t.Fatalf("targets = %+v", c.Targets)
	}
	// The Algo Bot's break_retest scope needs one of its evidence prefixes and no reaction.
	codes := map[string]bool{}
	for _, e := range c.Evidence {
		codes[e.Code] = true
	}
	for _, code := range []string{"m5_key_level_break", "m5_key_level_retest", "m5_break_accepted", "m5_retest_holds", "m5_retest_rejection", "displacement_grade", "htf_aligned", "structural_agreement"} {
		if !codes[code] {
			t.Errorf("evidence %q missing: %v", code, codes)
		}
	}
	if codes["m5_trendline_break"] {
		t.Errorf("a key-level setup carries trendline evidence: %v", codes)
	}
	if c.Reaction != nil {
		t.Errorf("break_retest carries no separate reaction: %+v", c.Reaction)
	}
	// Quality.Overall is exactly what live arbitration has always seen; the
	// measured quality is published in the components.
	if c.Quality.Overall != 0.75 {
		t.Errorf("Quality.Overall = %v, want the unchanged 0.75", c.Quality.Overall)
	}
	for _, key := range []string{"break_distance_atr", "break_body_ratio", "break_close_strength", "break_displacement_atr", "break_accept_closes",
		"retest_depth_atr", "retest_bars_after_accept", "rejection_wick_ratio", "rejection_close_strength", "reference_touches", "target_room_atr",
		"technical_rr", "technical_risk_pips", "confluence_stars"} {
		if _, ok := c.Quality.Components[key]; !ok {
			t.Errorf("quality component %q missing", key)
		}
	}
	// HTF 4 + touches 2 + wick 3 + displacement 3 + structure 3 = 15 -> 3 stars.
	if c.DetectorConfluence == nil || c.DetectorConfluence.SelectedStars != 3 {
		t.Fatalf("confluence = %+v", c.DetectorConfluence)
	}
	f := c.DetectorConfluence.Factors
	if !f.HTFAligned || !f.WickRejection || !f.StructuralAgreement || !f.DisplacementGrade || f.Touches != 2 || f.CHoCH || f.FibTouch {
		t.Fatalf("factors = %+v", f)
	}
}

func TestTrendlineCandidateCarriesTrendlineEvidence(t *testing.T) {
	bars, _ := bullishLine(0)
	got := newStrategy(t, nil).Evaluate(marketContext(bars, "up", "up"))
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	codes := map[string]bool{}
	for _, e := range got[0].Evidence {
		codes[e.Code] = true
	}
	if !codes["m5_trendline_break"] || !codes["m5_trendline_retest"] || codes["m5_key_level_break"] {
		t.Fatalf("evidence = %v", codes)
	}
}

// The measured facts decide the stars; the floor is the same one the Algo Bot
// applies, and a retest it refuses is reported with its reason.
func TestMeasuredFactorsDecideTheStarsAndTheFloor(t *testing.T) {
	bars, _ := bullishLevel(defaultLevel())
	// No HTF: wick 3 + displacement 3 + structure 3 + touches 2 = 11 -> 2 stars: published.
	two := newStrategy(t, nil).Evaluate(marketContext(bars, "up", "down"))
	if len(two) != 1 || two[0].DetectorConfluence.SelectedStars != 2 || two[0].DetectorConfluence.Factors.HTFAligned {
		t.Fatalf("unaligned: %+v", two)
	}
	// No HTF and the structure against the trade: 8 raw -> still 2? wick+displacement+touches = 8.
	// Remove the wick too (a rejection wick threshold the fixture does not meet) and only
	// displacement + touches remain: 5 -> 1 star, below the floor.
	strict := newStrategy(t, map[string]any{"rejection_wick_ratio": 0.9, "rejection_body_ratio": 0.5})
	analysis, candidates := strict.(*Strategy).Analyze(marketContext(bars, "down", "down"))
	if len(candidates) != 0 {
		t.Fatalf("a below-floor retest published: %+v", candidates[0].DetectorConfluence)
	}
	found := false
	for _, ep := range analysis.Episodes {
		if ep.Direction == market.Buy && ep.Reference.Kind == KindKeyLevel {
			found = true
			if ep.State != StateRetestConfirmed || ep.Reason != ReasonConfluenceFloor {
				t.Errorf("episode = %s/%s, want RETEST_CONFIRMED/%s", ep.State, ep.Reason, ReasonConfluenceFloor)
			}
		}
	}
	if !found || len(analysis.Setups) != 0 {
		t.Errorf("below-floor retest not reported: setups=%d", len(analysis.Setups))
	}
}

func TestIdentityIsStableAcrossFreshCandlesAndDistinctForANewEvent(t *testing.T) {
	bars, m := bullishLevel(defaultLevel())
	s := newStrategy(t, nil)
	a := s.Evaluate(marketContext(bars, "up", "up"))
	extended := append(append([]market.Candle(nil), bars...), market.Candle{Time: bars[len(bars)-1].Time + 300, Open: 4122.6, High: 4123.0, Low: 4122.4, Close: 4122.8})
	b := s.Evaluate(marketContext(extended, "up", "up"))
	if len(a) != 1 || len(b) != 1 || a[0].ID != b[0].ID || a[0].StructuralID != b[0].StructuralID {
		t.Fatalf("the same event changed identity: %v vs %v", a, b)
	}
	// A different break of the same level is a new event.
	late, lm := bullishLevel(levelOptions{secondTouch: true, breakClose: 4122.4, breakUpper: 0.3, acceptClose: 4123.4, touchLow: 4120.1, rejectClose: 4122.6, spikeUpper: 8, waitBars: 3})
	c := s.Evaluate(marketContext(late, "up", "up"))
	if len(c) != 1 || c[0].ID == a[0].ID {
		t.Fatalf("a later confirmation must be a distinct candidate: %v", c)
	}
	_ = m
	_ = lm
}

func TestConfigurationFailsClosed(t *testing.T) {
	bad := []map[string]any{
		{"breakout_accept_bars": 0.0},
		{"level_min_touches": 1.0},
		{"protected_structure": "anywhere"},
		{"minimum_break_body_ratio": 1.5},
		{"execution_stop_max_pips": 0.0},
		{"line_max_slope_atr": 0.005},
		{"atr_window_bars": 10.0},
	}
	for _, override := range bad {
		if _, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: brParams(override)}); err == nil {
			t.Errorf("%v accepted", override)
		}
	}
	params := brParams(nil)
	delete(params, "retest_max_bars")
	if _, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: params}); err == nil {
		t.Error("a missing key was accepted: there are no silent defaults")
	}
	if _, err := New(strategy.Config{ID: "crt", Version: Version, Parameters: brParams(nil)}); err == nil {
		t.Error("wrong strategy id accepted")
	}
}

func TestRequiresOnlyM5AndIgnoresAnEmptyContext(t *testing.T) {
	s := newStrategy(t, nil)
	if tf := s.RequiredTimeframes(); len(tf) != 1 || tf[0] != market.M5 {
		t.Fatalf("required timeframes = %v", tf)
	}
	if got := s.Evaluate(nil); got != nil {
		t.Fatal("nil context produced candidates")
	}
	if got := s.Evaluate(&analysiscontext.MarketContext{Symbol: "XAU"}); got != nil {
		t.Fatal("empty context produced candidates")
	}
}
