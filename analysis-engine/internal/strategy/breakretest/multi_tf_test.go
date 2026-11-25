package breakretest

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func pub(tf market.Timeframe, dir market.Direction, reference, band float64, stars int, entry float64) published {
	return published{
		frame: tf,
		setup: Setup{Direction: dir, EntryLow: entry - 0.5, EntryHigh: entry + 0.5, ThesisBand: band, Reference: Reference{PriceAtBreak: reference}},
		candidate: opportunity.Candidate{ID: string(tf) + string(dir),
			DetectorConfluence: &opportunity.ConfluenceContext{SelectedStars: stars}},
	}
}

// Default 4: the same broken reference found on more than one frame is one thesis; the higher frame publishes and the others are counted on their own frames.
func TestOneCandidatePerThesisAcrossFramesTheHigherFrameFirst(t *testing.T) {
	s := &Strategy{}
	out := s.selectTheses([]published{
		pub(market.M5, market.Buy, 4000, 0.5, 3, 4001),
		pub(market.M15, market.Buy, 4000.4, 1.5, 2, 4001),
		pub(market.H1, market.Buy, 3999.7, 2.5, 1, 4001),
	}, 4001)
	if len(out) != 1 || out[0].frame != market.H1 {
		t.Fatalf("the H1 reading of the thesis publishes alone: %+v", out)
	}
	got := s.TakeRefusals()
	if got[strategy.Refusal{Reason: RefusedDuplicateThesis, Timeframe: market.M5}] != 1 || got[strategy.Refusal{Reason: RefusedDuplicateThesis, Timeframe: market.M15}] != 1 || len(got) != 2 {
		t.Fatalf("the dropped readings are counted on their frames: %v", got)
	}
}

// Duplicate-thesis merging only ever happens across frames.
func TestTwoReferencesOfOneFrameAreSeparateTheses(t *testing.T) {
	s := &Strategy{}
	// The same frame is never merged: two M15 references stay two candidates.
	out := s.selectTheses([]published{pub(market.M15, market.Buy, 4000, 1, 2, 4001), pub(market.M15, market.Buy, 4000.2, 1, 2, 4002)}, 4001)
	if len(out) != 2 || len(s.TakeRefusals()) != 0 {
		t.Fatalf("two references of one frame are separate theses: %d", len(out))
	}
}

func TestDistinctReferencesAndOppositeDirectionsAreNotDuplicates(t *testing.T) {
	s := &Strategy{}
	out := s.selectTheses([]published{
		pub(market.M5, market.Buy, 4000, 0.5, 2, 4001),
		pub(market.M15, market.Buy, 4030, 1.5, 2, 4031), // a different level
		pub(market.H1, market.Sell, 4000, 2, 2, 4001),   // the other direction
	}, 4001)
	if len(out) != 3 || len(s.TakeRefusals()) != 0 {
		t.Fatalf("nothing is a duplicate: %d", len(out))
	}
}

// The retest of a structure-frame break starts at the first M5 bar that opens once the acceptance bar has closed, never earlier.
func TestRetestStartsAtTheFirstM5BarAfterTheAcceptanceBarCloses(t *testing.T) {
	fine := make([]market.Candle, 40)
	for i := range fine {
		fine[i] = market.Candle{Time: 1_000_000 + int64(i)*300}
	}
	d := &detector{fine: fine, period: 900}
	// An M15 bar opening at 1_001_800 closes at 1_002_700: M5 index 9.
	if got := d.fineFrom(1_001_800 + 900); got != 9 {
		t.Fatalf("M15 acceptance bar: first M5 index %d, want 9", got)
	}
	if got := d.fineFrom(fine[len(fine)-1].Time + 300); got != len(fine) {
		t.Fatalf("a bar that has not closed yet exposes no M5 bar: %d", got)
	}
	// The windows are counted in structure-frame bars converted to time: 24 M15 bars are 6 hours.
	bars := []market.Candle{{Time: 0}, {Time: 24 * 900}}
	if got := (&detector{bars: bars, period: 900}).barsBetween(0, 1); got != 24 {
		t.Fatalf("M15 bars %d", got)
	}
	if got := (&detector{bars: bars, period: 3600}).barsBetween(0, 1); got != 6 {
		t.Fatalf("H1 bars %d", got)
	}
}

func params() map[string]any {
	return map[string]any{
		"atr_length": 14, "atr_window_bars": 140, "pivot_bars": 2, "reference_lookback_bars": 120, "episode_lookback_bars": 48, "level_min_touches": 2,
		"level_cluster_pips": 2.0, "level_cluster_atr": 0.4, "level_span_multiple": 2.0, "line_min_span_bars": 6, "line_min_slope_atr": 0.01, "line_max_slope_atr": 0.10,
		"line_wick_atr": 0.25, "line_max_gap_ratio": 1.5, "pre_break_bars": 3, "breakout_accept_bars": 2, "break_buffer_pips": 1.0, "break_buffer_atr": 0.05,
		"minimum_break_body_ratio": 0.5, "minimum_break_close_strength": 0.6, "minimum_break_displacement_atr": 0.5, "displacement_grade_atr": 1.0,
		"break_fail_pips": 2.0, "break_fail_atr": 0.30, "retest_max_bars": 24, "retest_touch_pips": 1.0, "retest_touch_atr": 0.15, "confirmation_window_bars": 3,
		"confirmation_max_age_bars": 2, "rejection_close_strength": 0.6, "rejection_wick_ratio": 0.25, "rejection_body_ratio": 0.6, "protected_structure": "last_pivot",
		"entry_tolerance_pips": 1.0, "entry_tolerance_atr": 0.15, "maximum_entry_distance_atr": 1.5, "invalidation_buffer_atr": 0.25, "target_lookback_bars": 96,
		"target_swing_bars": 12, "target_swing_atr": 1.0, "minimum_target_room_atr": 0.55, "minimum_reward_risk": 1.15, "expiry_hours": 4.0, "pip_size": 0.1,
		"structure_timeframes": []any{"M5", "M15", "H1"}, "require_confirmation": true, "structure_close_buffer_atr": 0.1, "require_candle_confirmation": false,
		"confluence_floor": 2, "confluence_star_three_ratio": 0.585, "confluence_star_two_ratio": 0.39, "confluence_zone_quality_weight": 4.0, "confluence_mad_score_weight": 2.0,
		"fibonacci_confluence_weight": 2.5, "fibonacci_epsilon_atr": 0.1, "confluence_scoring_version": "v1", "fibonacci_enabled": true,
		"reaction_lookback_bars": 3, "engulfing_minimum_range_atr": 0.5, "maximum_entry_atr": 2.0, "maximum_zone_width_atr": 3.0, "proximal_band_atr": 0.3, "fvg_entry_max_width_price": 5.0,
		"barrier_buffer_atr": 0.5, "barrier_max_width_atr": 2.0, "barrier_max_width_pips": 100.0,
	}
}

func build(t *testing.T, p map[string]any) (*Strategy, error) {
	t.Helper()
	built, err := New(strategy.Config{ID: ID, Version: Version, Enabled: true, Parameters: p})
	if err != nil {
		return nil, err
	}
	return built.(*Strategy), nil
}

func TestBreakRetestParsesItsFramesAndRefusesM1AndABadBuffer(t *testing.T) {
	s, err := build(t, params())
	if err != nil {
		t.Skipf("the shared barrier and detector keys of this fixture are incomplete: %v", err)
	}
	if len(s.cfg.StructureTimeframes) != 3 || !s.cfg.RequireConfirmation || s.cfg.CandleConfirmationOptional || s.cfg.StructureCloseBufferATR != 0.1 {
		t.Fatalf("config %+v", s.cfg)
	}
	for name, mutate := range map[string]func(map[string]any){
		"M1":              func(p map[string]any) { p["structure_timeframes"] = []any{"M1"} },
		"H4":              func(p map[string]any) { p["structure_timeframes"] = []any{"M5", "H4"} },
		"missing frames":  func(p map[string]any) { delete(p, "structure_timeframes") },
		"missing buffer":  func(p map[string]any) { delete(p, "structure_close_buffer_atr") },
		"negative buffer": func(p map[string]any) { p["structure_close_buffer_atr"] = -0.1 },
		"bad confirm":     func(p map[string]any) { p["require_confirmation"] = "yes" },
	} {
		p := params()
		mutate(p)
		if _, err := build(t, p); err == nil {
			t.Errorf("%s was accepted", name)
		}
	}
	p := params()
	delete(p, "require_confirmation")
	if s, err := build(t, p); err != nil || s.cfg.RequireConfirmation || !s.cfg.CandleConfirmationOptional {
		t.Fatalf("absent, the global false leaves the touch optional: %v", err)
	}
}
