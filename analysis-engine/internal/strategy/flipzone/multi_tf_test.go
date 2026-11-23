package flipzone

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func cand(tf market.Timeframe, dir market.Direction, level, band float64, stars int, distance, entryGap float64) flipCandidate {
	return flipCandidate{tf: tf, direction: dir, levelPrice: level, levelBand: band, stars: stars, distance: distance, entryGap: entryGap}
}

// Default 4 on one side: the same flip level on more than one frame is one thesis, the higher frame publishes, and the others are counted.
func TestOneCandidatePerFlipThesisAcrossFramesTheHigherFrameFirst(t *testing.T) {
	s := &Strategy{}
	out := s.selectPerSide([]flipCandidate{
		cand(market.M5, market.Buy, 4000, 0.5, 3, 1, 1),
		cand(market.M15, market.Buy, 4000.4, 1.5, 2, 1, 1),
		cand(market.H1, market.Buy, 3999.7, 2.5, 1, 1, 1),
	})
	if len(out) != 1 {
		t.Fatalf("one candidate per side at most: %d", len(out))
	}
	got := s.TakeRefusals()
	if got[strategy.Refusal{Reason: RefusedDuplicateThesis, Timeframe: market.M5}] != 1 || got[strategy.Refusal{Reason: RefusedDuplicateThesis, Timeframe: market.M15}] != 1 || len(got) != 2 {
		t.Fatalf("the two dropped candidates are counted on their own frames: %v", got)
	}
}

func TestOnePerSideWithTheHighestStarsAcrossDifferentLevels(t *testing.T) {
	s := &Strategy{}
	buy := cand(market.M5, market.Buy, 4000, 0.4, 2, 5, 1)
	better := cand(market.M5, market.Buy, 4020, 0.4, 3, 9, 1)
	sell := cand(market.M15, market.Sell, 4050, 1, 1, 3, 1)
	for i, c := range []*flipCandidate{&buy, &better, &sell} {
		c.candidate.ID = string(rune('a' + i))
	}
	out := s.selectPerSide([]flipCandidate{buy, better, sell})
	if len(out) != 2 || out[0].ID != "b" || out[1].ID != "c" {
		t.Fatalf("BUY keeps the 3-star level, SELL its only one: %+v", out)
	}
	if len(s.TakeRefusals()) != 0 {
		t.Fatal("distinct levels are not duplicates")
	}
}

func TestSameFrameAndOppositeDirectionAreNeverOneThesis(t *testing.T) {
	s := &Strategy{}
	s.selectPerSide([]flipCandidate{cand(market.M5, market.Buy, 4000, 1, 2, 1, 1), cand(market.M5, market.Buy, 4000.2, 1, 2, 2, 1), cand(market.M15, market.Sell, 4000, 1, 2, 1, 1)})
	if len(s.TakeRefusals()) != 0 {
		t.Fatal("two zones of one frame, and BUY against SELL, are separate")
	}
}

// The retest may react from the bar after the formation bar on M5 (as ZoneReaction did), and from the first M5 bar after the formation bar's CLOSE on a higher frame.
func TestRetestKnownFromFollowsTheFormationBarsClose(t *testing.T) {
	m5 := make([]market.Candle, 40)
	for i := range m5 {
		m5[i] = market.Candle{Time: 1_000_000 + int64(i)*300}
	}
	if got := reactionKnownFrom(m5, &context.LegacyFrame{Bars: m5}, market.M5, 7); got != 8 {
		t.Fatalf("M5: the bar after the formation bar, got %d", got)
	}
	m15 := []market.Candle{{Time: 1_000_000}, {Time: 1_000_900}, {Time: 1_001_800}, {Time: 1_002_700}}
	if got := reactionKnownFrom(m5, &context.LegacyFrame{Bars: m15}, market.M15, 2); got != 9 {
		t.Fatalf("M15 bar 2 (open 1_001_800) closes at 1_002_700: M5 index 9, got %d", got)
	}
	if got := reactionKnownFrom(m5[:5], &context.LegacyFrame{Bars: m15}, market.M15, 3); got != 5 {
		t.Fatalf("no M5 bar sees a frame bar that has not closed yet: %d", got)
	}
}

func params() map[string]any {
	return map[string]any{
		"invalidation_buffer_atr": 0.5, "minimum_target_distance_atr": 1.0, "expiry_hours": 24.0, "breakout_accept_bars": 2, "structure_timeframes": []any{"M5", "M15", "H1"},
		"require_confirmation": true, "structure_close_buffer_atr": 0.1, "require_candle_confirmation": false,
		"pip_size": 0.1, "maximum_entry_atr": 2.0, "maximum_zone_width_atr": 3.0, "proximal_band_atr": 0.3, "fvg_entry_max_width_price": 5.0, "confluence_floor": 2,
		"confluence_star_three_ratio": 0.585, "confluence_star_two_ratio": 0.39, "confluence_zone_quality_weight": 4.0, "confluence_mad_score_weight": 2.0,
		"fibonacci_confluence_weight": 2.5, "fibonacci_epsilon_atr": 0.1, "confluence_scoring_version": "v1", "fibonacci_enabled": true,
		"reaction_lookback_bars": 3, "engulfing_minimum_range_atr": 0.5,
	}
}

func build(t *testing.T, p map[string]any) (*Strategy, error) {
	t.Helper()
	built, err := New(strategy.Config{ID: ID, Version: "v6", Enabled: true, Parameters: p})
	if err != nil {
		return nil, err
	}
	return built.(*Strategy), nil
}

func TestFlipZoneParsesItsFramesAndRefusesM1AndABadBuffer(t *testing.T) {
	s, err := build(t, params())
	if err != nil {
		t.Fatal(err)
	}
	if len(s.cfg.StructureTimeframes) != 3 || !s.cfg.RequireConfirmation || s.cfg.Legacy.CandleConfirmationOptional || s.cfg.StructureCloseBufferATR != 0.1 {
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
	if s, err := build(t, p); err != nil || s.cfg.RequireConfirmation || !s.cfg.Legacy.CandleConfirmationOptional {
		t.Fatalf("absent, the global false leaves the touch optional: %v", err)
	}
}
