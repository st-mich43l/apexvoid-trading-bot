package sessionlevel

import (
	"fmt"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// sessionParams is the strategy's own parameter set as config/analysis.yml declares
// it (XAU scale), the session grid the engine injects, merged with the shared
// detector contract.
func sessionParams(override map[string]any) map[string]any {
	own := sessionOwn()
	for k, v := range override {
		own[k] = v
	}
	return legacyfixture.Params(own)
}

func sessionOwn() map[string]any {
	return map[string]any{
		"atr_length": 14.0, "atr_window_bars": 140.0, "pivot_bars": 2.0, "episode_lookback_bars": 48.0,
		"asia_start_hour": 22.0, "london_start_hour": 7.0, "ny_start_hour": 13.0, "daily_rollover_utc_hour": 21.0,
		"session_start_tolerance_minutes": 90.0, "session_end_tolerance_minutes": 90.0, "session_max_gap_minutes": 60.0, "session_minimum_coverage": 0.8,
		"period_start_tolerance_minutes": 120.0, "period_end_tolerance_minutes": 120.0, "period_max_gap_minutes": 150.0, "period_minimum_coverage": 0.85,
		"session_max_age_hours": 30.0, "day_max_age_hours": 72.0, "week_max_age_hours": 168.0,
		"touch_tolerance_pips": 2.0, "touch_tolerance_atr": 0.10, "approach_lookback_bars": 24.0, "approach_min_distance_atr": 0.5,
		"touch_separation_bars": 3.0, "maximum_prior_touches": 1.0, "hold_buffer_pips": 1.0, "hold_buffer_atr": 0.05,
		"fail_buffer_pips": 3.0, "fail_buffer_atr": 0.25, "sweep_threshold_pips": 3.0, "sweep_threshold_atr": 0.10,
		"reclaim_buffer_pips": 1.0, "reclaim_buffer_atr": 0.05, "reclaim_max_bars": 6.0, "breakout_accept_closes": 2.0,
		"breakout_buffer_pips": 2.0, "breakout_buffer_atr": 0.15, "rejection_close_strength": 0.6, "rejection_wick_ratio": 0.25,
		"rejection_body_ratio": 0.6, "displacement_grade_atr": 1.0, "confirmation_window_bars": 3.0, "confirmation_max_age_bars": 2.0,
		"max_episodes_per_level": 3.0, "entry_band_atr": 0.5, "entry_tolerance_pips": 1.0, "entry_tolerance_atr": 0.15,
		"maximum_entry_distance_atr": 1.5, "invalidation_buffer_atr": 0.25, "target_lookback_bars": 96.0, "target_swing_bars": 12.0,
		"target_swing_atr": 1.0, "minimum_target_room_atr": 0.55, "minimum_reward_risk": 1.15, "expiry_hours": 4.0,
		"pip_size": 0.1, "execution_stop_max_pips": 120.0,
	}
}

func newStrategy(t *testing.T, override map[string]any) strategy.Strategy {
	t.Helper()
	s, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: sessionParams(override)})
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// marketContext carries the closed M5 candles, the engine's structural read of the
// M5 frame and an HTF bias, the way the engine hands them to a strategy.
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
	bars, m := firstTouchDay(opts{})
	got := newStrategy(t, nil).Evaluate(marketContext(bars, "up", "up"))
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	c := got[0]
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	if c.Strategy != ID || c.StrategyVersion != "v4" || c.Direction != market.Buy || c.Symbol != "XAU" {
		t.Fatalf("identity = %+v", c)
	}
	want := fmt.Sprintf("technique:session_level:buy:level:LONDON_L:%d:first_touch_rejection:%d", dayAnchor+7*3600, bars[m.touch].Time)
	if c.StructuralID != want {
		t.Fatalf("structural id = %q, want %q", c.StructuralID, want)
	}
	if c.FormedAt != dayAnchor+13*3600 || c.CreatedAt != bars[m.confirm].Time || c.ExpiresAt != c.CreatedAt+4*3600 {
		t.Fatalf("formed %d created %d expires %d", c.FormedAt, c.CreatedAt, c.ExpiresAt)
	}
	if c.Invalidation.Label != "session_level_invalidated" || !(float64(c.Invalidation.Price) < 4104) {
		t.Fatalf("invalidation = %+v", c.Invalidation)
	}
	if len(c.Targets) != 1 || float64(c.Targets[0].Price.Price) < 4118 || c.Targets[0].Price.Label != "opposing_liquidity" {
		t.Fatalf("targets = %+v", c.Targets)
	}
	// The Algo Bot's session_level scope needs a session_level_ evidence prefix AND a
	// confirmed reaction.
	codes := map[string]bool{}
	for _, e := range c.Evidence {
		codes[e.Code] = true
	}
	for _, code := range []string{"session_level_london_l", "session_level_reaction_confirmed", "session_level_first_touch_rejection", "htf_aligned", "structural_agreement"} {
		if !codes[code] {
			t.Errorf("evidence %q missing: %v", code, codes)
		}
	}
	if c.Reaction == nil {
		t.Fatal("a confirmed reaction must carry its confirmation")
	}
	if c.Reaction.ZoneID != c.StructuralID || c.Reaction.TouchBarTime != bars[m.touch].Time || c.Reaction.ConfirmationBarTime != bars[m.confirm].Time || c.Reaction.ReactionType != "rejection" {
		t.Fatalf("reaction = %+v", c.Reaction)
	}
	if c.Reaction.Pattern != "strong_reclaim" && c.Reaction.Pattern != "wick_rejection" {
		t.Fatalf("pattern %q: the Algo Bot contract only knows the legacy pattern names", c.Reaction.Pattern)
	}
	if c.DetectorConfluence == nil || c.Quality.Overall != float64(c.DetectorConfluence.SelectedStars)/3 {
		t.Errorf("Quality.Overall = %v with %+v", c.Quality.Overall, c.DetectorConfluence)
	}
	for _, key := range []string{"level_type", "level_age_bars", "period_completeness", "rejection_strength", "reclaim_depth_atr", "reaction_freshness",
		"actual_level_touches", "structure_alignment", "target_room_r", "level_integrity", "technical_risk_pips", "touch_penetration_atr"} {
		if _, ok := c.Quality.Components[key]; !ok {
			t.Errorf("quality component %q missing: %v", key, c.Quality.Components)
		}
	}
	if _, ok := c.Quality.Components["sweep_depth_atr"]; ok {
		t.Error("a first touch has no sweep: sweep_depth_atr must be left out, not zeroed")
	}
	if c.Quality.Components["level_type"] != 1 || c.Quality.Components["actual_level_touches"] != 1 || c.Quality.Components["period_completeness"] != 1 {
		t.Errorf("components = %v", c.Quality.Components)
	}
	f := c.DetectorConfluence.Factors
	if !f.HTFAligned || !f.StructuralAgreement || !f.SessionContext || f.Touches != 1 || f.CHoCH || f.FibTouch {
		t.Fatalf("factors = %+v", f)
	}
}

func TestSweepReclaimCandidateUsesTheSweepPatternAndOwnComponents(t *testing.T) {
	bars, m := sweepDay(opts{})
	got := newStrategy(t, nil).Evaluate(marketContext(bars, "up", "up"))
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	c := got[0]
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	if c.Reaction.Pattern != "sweep_reclaim" || c.Reaction.TouchBarTime != bars[m.sweep].Time {
		t.Fatalf("reaction = %+v", c.Reaction)
	}
	if _, ok := c.Quality.Components["sweep_depth_atr"]; !ok {
		t.Fatalf("a sweep must publish its depth: %v", c.Quality.Components)
	}
	if _, ok := c.Quality.Components["touch_penetration_atr"]; ok {
		t.Fatal("a sweep has no first-touch penetration component")
	}
	if c.StructuralID == "" || c.StructuralID == fmt.Sprintf("technique:session_level:buy:level:LONDON_L:%d:first_touch_rejection:%d", dayAnchor+7*3600, bars[m.sweep].Time) {
		t.Fatalf("structural id = %q", c.StructuralID)
	}
}

// A component the engine cannot read is left out, never replaced by a neutral value.
func TestUnavailableStructureIsOmittedNotInvented(t *testing.T) {
	// A wick rejection with a real displacement, so the setup clears the confluence
	// floor without the structural read.
	bars, _ := firstTouchDay(opts{rejectClose: 4107.4, rejectOpen: 4104.9, rejectLow: 4103.85})
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

// A level whose availability precedes the M5 history is built from the hourly
// candles, but the M5 window cannot show what touched it since: no touch history
// is claimed, and no first touch is judged.
func TestTheTouchCountIsNotClaimedWhenTheHistoryCannotShowIt(t *testing.T) {
	bars, _ := firstTouchDay(opts{})
	l := Level{ID: "level:PDL:1", Name: "PDL", Kind: KindDay, Side: SideSupport, Price: 4104, AvailableAt: bars[0].Time - 3600, PeriodEnd: bars[0].Time - 3600}
	d := newDetector(testConfig(), bars, []Level{l}, market.Buy)
	if len(d.levels) != 1 || d.levels[0].known {
		t.Fatalf("a level available before the M5 history must not claim a known touch history: %+v", d.levels)
	}
	for _, ep := range d.run() {
		if ep.Model == ModelFirstTouch && (ep.Setup != nil || ep.Reason != ReasonHistoryIndeterminate) {
			t.Fatalf("a first touch was judged without knowing the level's touch history: %+v", ep)
		}
	}
}

func TestBearishTwinIsASellWithTheMirroredEvidence(t *testing.T) {
	bars, _ := firstTouchDay(opts{})
	got := newStrategy(t, nil).Evaluate(marketContext(reflectPrices(bars, 4112), "down", "down"))
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	c := got[0]
	if err := c.Validate(); err != nil {
		t.Fatal(err)
	}
	if c.Direction != market.Sell || !(float64(c.Invalidation.Price) > 8224-4104) {
		t.Fatalf("direction %v invalidation %+v", c.Direction, c.Invalidation)
	}
	codes := map[string]bool{}
	for _, e := range c.Evidence {
		codes[e.Code] = true
	}
	if !codes["session_level_london_h"] {
		t.Fatalf("evidence = %v", codes)
	}
}

func TestEvaluateIsAPureFunctionOfTheContext(t *testing.T) {
	bars, _ := firstTouchDay(opts{})
	s := newStrategy(t, nil)
	a := s.Evaluate(marketContext(bars, "up", "up"))
	b := s.Evaluate(marketContext(bars, "up", "up"))
	if len(a) != 1 || len(b) != 1 || a[0].ID != b[0].ID {
		t.Fatalf("two evaluations differ: %+v vs %+v", a, b)
	}
	// The persistent id is stable across later evaluations of the same reaction.
	later, _ := firstTouchDay(opts{afterBars: 2})
	c := s.Evaluate(marketContext(later, "up", "up"))
	if len(c) != 1 || c[0].StructuralID != a[0].StructuralID {
		t.Fatalf("structural id changed: %q vs %q", c[0].StructuralID, a[0].StructuralID)
	}
}

func TestNilEmptyAndWrongStrategyInputs(t *testing.T) {
	s := newStrategy(t, nil)
	if got := s.Evaluate(nil); got != nil {
		t.Fatalf("nil context -> %v", got)
	}
	if got := s.Evaluate(marketContext(nil, "", "")); got != nil {
		t.Fatalf("no candles -> %v", got)
	}
	if _, err := New(strategy.Config{ID: "ifvg", Parameters: sessionParams(nil)}); err == nil {
		t.Fatal("the wrong strategy id must be refused")
	}
	if got := s.RequiredTimeframes(); len(got) != 1 || got[0] != market.M5 || s.ID() != ID {
		t.Fatalf("timeframes %v id %v", got, s.ID())
	}
}

// Every coefficient is required: a missing key fails construction instead of
// silently falling back to a code default.
func TestEveryCoefficientIsRequiredAndValidated(t *testing.T) {
	for key := range sessionOwn() {
		if key == "execution_stop_max_pips" {
			continue // optional: absent disables the pre-check
		}
		without := sessionParams(nil)
		delete(without, key)
		if _, err := ParseConfig(without); err == nil {
			t.Errorf("a missing %q was accepted", key)
		}
	}
	if _, err := ParseConfig(sessionParams(nil)); err != nil {
		t.Fatal(err)
	}
	without := sessionParams(nil)
	delete(without, "execution_stop_max_pips")
	if cfg, err := ParseConfig(without); err != nil || cfg.ExecutionStopMaxPips != 0 {
		t.Fatalf("the stop cap is optional: %v %v", cfg.ExecutionStopMaxPips, err)
	}
	for name, bad := range map[string]map[string]any{
		"zero pip size":              {"pip_size": 0.0},
		"inverted atr window":        {"atr_window_bars": 10.0},
		"same session hours":         {"london_start_hour": 22.0},
		"hour out of range":          {"ny_start_hour": 25.0},
		"fraction above one":         {"rejection_close_strength": 1.5},
		"negative tolerance":         {"session_start_tolerance_minutes": -1.0},
		"zero confirmation window":   {"confirmation_window_bars": 0.0},
		"zero stop cap when present": {"execution_stop_max_pips": 0.0},
	} {
		if _, err := ParseConfig(sessionParams(bad)); err == nil {
			t.Errorf("%s was accepted", name)
		}
	}
}

func TestConfluenceFloorStillApplies(t *testing.T) {
	bars, _ := firstTouchDay(opts{})
	s := newStrategy(t, map[string]any{"confluence_floor": 3.0})
	ctx := marketContext(bars, "", "")
	ctx.Legacy = &analysiscontext.LegacyRead{LocalStructure: "", HTFBias: "", AllowCounterTrend: true}
	ctx.Timeframes[market.M5].Legacy = nil
	if got := s.Evaluate(ctx); len(got) != 0 {
		t.Fatalf("a setup below the confluence floor was published: %+v", got[0].Quality)
	}
}
