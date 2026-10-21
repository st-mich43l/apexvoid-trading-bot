package ifvg

import (
	"fmt"
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// ifvgParams is the strategy's own parameter set as config/analysis.yml declares it
// (XAU scale), merged with the shared detector contract the engine injects.
func ifvgParams(override map[string]any) map[string]any {
	own := map[string]any{
		"atr_length": 14.0, "atr_window_bars": 140.0, "pivot_bars": 2.0, "episode_lookback_bars": 48.0,
		"minimum_gap_atr": 0.5, "minimum_gap_pips": 2.0, "minimum_origin_body_atr": 0.5, "minimum_origin_body_ratio": 0.5,
		"maximum_inversion_bars": 72.0, "minimum_break_pips": 1.0, "minimum_break_atr": 0.05,
		"minimum_inversion_body_ratio": 0.5, "minimum_inversion_close_strength": 0.6, "minimum_inversion_displacement_atr": 0.5,
		"displacement_grade_atr": 1.0, "role_flip_fail_pips": 2.0, "role_flip_fail_atr": 0.30,
		"retest_max_bars": 24.0, "retest_touch_pips": 1.0, "retest_touch_atr": 0.15, "confirmation_window_bars": 3.0,
		"confirmation_max_age_bars": 2.0, "rejection_close_strength": 0.6, "rejection_wick_ratio": 0.25, "rejection_body_ratio": 0.6,
		"max_retests_per_inversion": 2.0, "retest_departure_atr": 1.0, "entry_tolerance_pips": 1.0, "entry_tolerance_atr": 0.15,
		"maximum_entry_distance_atr": 1.5, "invalidation_buffer_atr": 0.25, "target_lookback_bars": 96.0, "target_swing_bars": 12.0,
		"target_swing_atr": 1.0, "minimum_target_room_atr": 0.55, "minimum_reward_risk": 1.15, "expiry_hours": 4.0,
		"execution_stop_max_pips": 120.0,
	}
	for k, v := range override {
		own[k] = v
	}
	return legacyfixture.Params(own)
}

func newStrategy(t *testing.T, override map[string]any) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: ifvgParams(override)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// marketContext carries the closed M5 candles, the engine's structural read of the
// M5 frame and an HTF bias, the way the engine hands them to a strategy.
func marketContext(m5 []market.Candle, structure, htf string) *analysiscontext.MarketContext {
	return marketContextWith(m5, structure, htf, nil)
}

// marketContextWith also hands the strategy the finalized session levels the engine keeps in the frame.
func marketContextWith(m5 []market.Candle, structure, htf string, sessions []techniquezone.SessionRef) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU",
		Legacy: &analysiscontext.LegacyRead{LocalStructure: structure, HTFBias: htf, AllowCounterTrend: true},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5: {Timeframe: market.M5, Candles: m5, Legacy: &analysiscontext.LegacyFrame{Structure: structure, Bars: m5, DetectorATR: 2, Sessions: sessions}},
		},
	}
}

func TestCandidateCarriesTheTechnicalContractAndPassesValidation(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
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
	want := fmt.Sprintf("technique:ifvg:buy:fvg:bearish:%d:%d", bars[m.c].Time, bars[m.invert].Time)
	if c.StructuralID != want {
		t.Fatalf("structural id = %q, want %q", c.StructuralID, want)
	}
	if c.FormedAt != bars[m.invert].Time || c.CreatedAt != bars[m.confirm].Time || c.ExpiresAt != c.CreatedAt+4*3600 {
		t.Fatalf("formed %d created %d expires %d", c.FormedAt, c.CreatedAt, c.ExpiresAt)
	}
	if c.Invalidation.Label != "ifvg_inversion_failed" || !(float64(c.Invalidation.Price) < 4112) {
		t.Fatalf("invalidation = %+v", c.Invalidation)
	}
	if len(c.Targets) != 1 || float64(c.Targets[0].Price.Price) < 4130 || c.Targets[0].Price.Label != "opposing_structure" {
		t.Fatalf("targets = %+v", c.Targets)
	}
	// The Algo Bot's ifvg scope needs an m5_ifvg_ evidence prefix AND a confirmed reaction.
	codes := map[string]bool{}
	for _, e := range c.Evidence {
		codes[e.Code] = true
	}
	for _, code := range []string{"m5_ifvg_gap", "m5_ifvg_inversion_confirmed", "m5_ifvg_retest", "m5_ifvg_rejection_confirmed", "displacement_grade", "htf_aligned", "structural_agreement"} {
		if !codes[code] {
			t.Errorf("evidence %q missing: %v", code, codes)
		}
	}
	if c.Reaction == nil {
		t.Fatal("a confirmed iFVG must carry its reaction confirmation")
	}
	if c.Reaction.ZoneID != c.StructuralID || c.Reaction.TouchBarTime != bars[m.touch].Time || c.Reaction.ConfirmationBarTime != bars[m.confirm].Time || c.Reaction.ReactionType != "rejection" {
		t.Fatalf("reaction = %+v", c.Reaction)
	}
	if c.Reaction.Pattern != "wick_rejection" {
		t.Fatalf("pattern %q: the Algo Bot contract only knows the legacy pattern names", c.Reaction.Pattern)
	}
	// Quality.Overall keeps the stars/3 scale arbitration has always seen; the
	// measured quality is in the components, and nothing is fabricated.
	if c.DetectorConfluence == nil || c.Quality.Overall != float64(c.DetectorConfluence.SelectedStars)/3 {
		t.Errorf("Quality.Overall = %v with %+v", c.Quality.Overall, c.DetectorConfluence)
	}
	for _, key := range []string{"gap_width_atr", "origin_displacement_atr", "inversion_body_ratio", "inversion_displacement_atr", "retest_age_bars",
		"retest_penetration_atr", "rejection_strength", "structure_alignment", "target_room_r", "zone_integrity", "retest_touches", "technical_risk_pips"} {
		if _, ok := c.Quality.Components[key]; !ok {
			t.Errorf("quality component %q missing", key)
		}
	}
	for key := range c.Quality.Components {
		if key == "inversion_strength" || key == "freshness" {
			t.Errorf("the v2 fabricated component %q is back", key)
		}
	}
	f := c.DetectorConfluence.Factors
	if !f.HTFAligned || !f.WickRejection || !f.StructuralAgreement || !f.DisplacementGrade || f.Touches != 1 || f.CHoCH || f.FibTouch {
		t.Fatalf("factors = %+v", f)
	}
}

// A component the engine cannot read is left out, never replaced by a neutral value.
func TestStructureAlignmentIsOmittedWhenTheEngineHasNoStructuralRead(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	ctx := marketContext(bars, "", "up")
	ctx.Timeframes[market.M5].Legacy = nil
	got := newStrategy(t, nil).Evaluate(ctx)
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	if _, ok := got[0].Quality.Components["structure_alignment"]; ok {
		t.Fatalf("structure_alignment was published without a structural read: %v", got[0].Quality.Components)
	}
}

func TestBearishTwinIsASellWithABullishOriginalGap(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	got := newStrategy(t, nil).Evaluate(marketContext(reflectPrices(bars, 4110), "down", "down"))
	if len(got) != 1 || got[0].Direction != market.Sell {
		t.Fatalf("candidates = %+v", got)
	}
	if !strings.HasPrefix(got[0].StructuralID, "technique:ifvg:sell:fvg:bullish:") {
		t.Fatalf("structural id %q", got[0].StructuralID)
	}
	if got[0].Reaction.TouchBarTime != bars[m.touch].Time {
		t.Fatalf("reaction %+v", got[0].Reaction)
	}
}

// The confluence floor is the same one the Algo Bot applies (min_confluence 2), and
// the measured facts decide it.
func TestMeasuredFactorsDecideTheStarsAndTheFloor(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	// No HTF, structure against, no session liquidity swept: touches 1 + wick 3 + displacement 3 = 7 -> 1 star,
	// below the floor. The session context is no longer a constant two points.
	analysis, candidates := newStrategy(t, nil).(*Strategy).Analyze(marketContext(bars, "down", "down"))
	if len(candidates) != 0 {
		t.Fatalf("a setup with only its own candle evidence published: %+v", candidates[0].DetectorConfluence)
	}
	_ = analysis
	// With the higher timeframe behind it: 4 + 1 + 3 + 3 = 11 -> 2 stars.
	two := newStrategy(t, nil).Evaluate(marketContext(bars, "down", "up"))
	if len(two) != 1 || two[0].DetectorConfluence.SelectedStars != 2 || two[0].Quality.Overall != 2.0/3 || two[0].DetectorConfluence.Factors.SessionContext {
		t.Fatalf("aligned: %+v", two)
	}
	// A stricter wick and body bar removes the wick credit: 4+1+3 = 8 is still two stars, but without the
	// alignment it is 1+3 = 4 -> 1 star, below the floor.
	s := newStrategy(t, nil).(*Strategy)
	s.cfg.RejectionWickRatio = 0.9
	s.cfg.RejectionBodyRatio = 0.99
	if _, candidates := s.Analyze(marketContext(bars, "down", "down")); len(candidates) != 0 {
		t.Fatalf("a below-floor setup published: %+v", candidates[0].DetectorConfluence)
	}
}

// The session context is the sweep of a FINALIZED session level during the move that built the setup.
func TestSessionContextIsAMeasuredSweepOfFinalizedLiquidity(t *testing.T) {
	bars, m := bullishIFVG(defaultOpts())
	touch := bars[m.touch]
	level := touch.Low + 0.3 // the retest runs 0.3 below it and the rejection closes back above; the inversion move took it first
	available := bars[m.invert].Time
	swept := []techniquezone.SessionRef{{Name: "ASIA_L", Price: level, AvailableAt: available}}
	// Unaligned (7 points) the sell-side sweep of a finalized Asia low is the 2 that lifts it to the floor.
	got := newStrategy(t, nil).Evaluate(marketContextWith(bars, "down", "down", swept))
	if len(got) != 1 || !got[0].DetectorConfluence.Factors.SessionContext || got[0].DetectorConfluence.SelectedStars != 2 {
		t.Fatalf("a swept and reclaimed finalized session low is evidence for the BUY: %+v", got)
	}
	found := false
	for _, e := range got[0].Evidence {
		found = found || e.Code == "session_liquidity_swept"
	}
	if !found {
		t.Fatal("the sweep must be recorded as evidence")
	}
	// A sweep on the retest candle itself is the rejection wick, already credited as such: one event, not a second fact.
	retest := []techniquezone.SessionRef{{Name: "ASIA_L", Price: level, AvailableAt: bars[m.invert].Time + 300}}
	if got := newStrategy(t, nil).Evaluate(marketContextWith(bars, "down", "down", retest)); len(got) != 0 {
		t.Fatalf("the retest's own wick is not session context: %+v", got[0].DetectorConfluence.Factors)
	}
	// A developing extreme (no AvailableAt), a level that closed after the setup, or a high (buy-side liquidity) is not.
	for name, refs := range map[string][]techniquezone.SessionRef{
		"developing":  {{Name: "ASIA_L", Price: level}},
		"too late":    {{Name: "ASIA_L", Price: level, AvailableAt: bars[m.confirm].Time + 600}},
		"wrong side":  {{Name: "ASIA_H", Price: level, AvailableAt: available}},
		"not reached": {{Name: "ASIA_L", Price: 4000, AvailableAt: available}},
	} {
		if got := newStrategy(t, nil).Evaluate(marketContextWith(bars, "down", "down", refs)); len(got) != 0 {
			t.Errorf("%s: not session liquidity swept for this trade, must not lift the setup: %+v", name, got[0].DetectorConfluence.Factors)
		}
	}
}

func TestAnalyzeAndEvaluateAgreeAndEvaluationIsRepeatable(t *testing.T) {
	bars, _ := bullishIFVG(defaultOpts())
	s := newStrategy(t, nil).(*Strategy)
	ctx := marketContext(bars, "up", "up")
	a1, c1 := s.Analyze(ctx)
	_, c2 := s.Analyze(ctx)
	e := s.Evaluate(ctx)
	if len(a1.Setups) != len(c1) || len(c1) != 1 || len(c2) != 1 || len(e) != 1 || c1[0].ID != c2[0].ID || c1[0].ID != e[0].ID {
		t.Fatalf("candidate ids differ: %v %v %v", c1, c2, e)
	}
}

func TestConstructionFailsOnAMissingOrInvalidParameter(t *testing.T) {
	for _, key := range []string{"minimum_gap_atr", "minimum_inversion_body_ratio", "maximum_inversion_bars", "target_swing_bars", "retest_departure_atr"} {
		params := ifvgParams(nil)
		delete(params, key)
		if _, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: params}); err == nil {
			t.Errorf("a missing %q must fail construction", key)
		}
	}
	if _, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: ifvgParams(map[string]any{"minimum_inversion_close_strength": 1.5})}); err == nil {
		t.Error("an out-of-range ratio must fail construction")
	}
	if _, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: ifvgParams(map[string]any{"execution_stop_max_pips": 0.0})}); err == nil {
		t.Error("a zero stop cap must fail construction: omit the key to disable the pre-check")
	}
	if _, err := New(strategy.Config{ID: "supply", Version: Version, Enabled: true, Parameters: ifvgParams(nil)}); err == nil {
		t.Error("a wrong strategy id must fail construction")
	}
}
