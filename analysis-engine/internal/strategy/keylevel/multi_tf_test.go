package keylevel

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

func pub(tf market.Timeframe, dir market.Direction, level, band float64, stars int, distance, entryGap float64) published {
	return published{tf: tf, direction: dir, levelPrice: level, levelBand: band, stars: stars, distance: distance, entryGap: entryGap}
}

// Default 4: the same level on more than one frame is one thesis; the higher frame publishes, then more stars, then the nearer entry; the others are counted.
func TestOneCandidatePerLevelThesisAcrossFramesByTheDuplicateOrder(t *testing.T) {
	s := &Strategy{}
	found := []published{
		pub(market.M5, market.Buy, 4000.0, 0.4, 3, 1, 1),
		pub(market.M15, market.Buy, 4000.3, 1.5, 2, 1, 1),
		pub(market.H1, market.Buy, 3999.8, 2.5, 1, 1, 1),
	}
	best := s.selectOne(found)
	if best == nil || best.tf != market.H1 {
		t.Fatalf("the higher frame wins whatever the stars: %+v", best)
	}
	got := s.TakeRefusals()
	if got[strategy.Refusal{Reason: RefusedDuplicateThesis, Timeframe: market.M5}] != 1 || got[strategy.Refusal{Reason: RefusedDuplicateThesis, Timeframe: market.M15}] != 1 || len(got) != 2 {
		t.Fatalf("the two dropped candidates are counted on their own frames: %v", got)
	}
	// On one frame, more stars first; at equal stars the nearer entry.
	if best := s.selectOne([]published{pub(market.M15, market.Buy, 4000, 1, 2, 1, 9), pub(market.M5, market.Buy, 4000, 1, 3, 1, 1)}); best.tf != market.M15 {
		t.Fatalf("M15 over M5: %+v", best)
	}
}

// Only the same level, the same direction and different frames are one thesis.
func TestDistinctLevelsDirectionsAndSameFrameCandidatesAreNotDuplicates(t *testing.T) {
	s := &Strategy{}
	far := []published{pub(market.M5, market.Buy, 4000, 0.4, 2, 1, 1), pub(market.M15, market.Buy, 4010, 1, 2, 2, 1)}
	if best := s.selectOne(far); best == nil || len(s.TakeRefusals()) != 0 {
		t.Fatal("levels 10 apart are two theses: nothing is dropped as a duplicate")
	}
	opposite := []published{pub(market.M5, market.Buy, 4000, 1, 2, 1, 1), pub(market.M15, market.Sell, 4000, 1, 2, 1, 1)}
	s.selectOne(opposite)
	if len(s.TakeRefusals()) != 0 {
		t.Fatal("BUY and SELL off one level are not one thesis")
	}
	sameFrame := []published{pub(market.M5, market.Buy, 4000, 1, 2, 1, 1), pub(market.M5, market.Buy, 4000.2, 1, 2, 2, 1)}
	s.selectOne(sameFrame)
	if len(s.TakeRefusals()) != 0 {
		t.Fatal("two levels of one frame are two structures, never a duplicate")
	}
}

// Across theses the existing rule holds: the highest stars, the nearest level at equal stars, and the first found at a tie.
func TestAcrossLevelsTheHighestStarsWinWithTheNearestLevelAtATie(t *testing.T) {
	s := &Strategy{}
	best := s.selectOne([]published{pub(market.M5, market.Buy, 4000, 0.4, 2, 5, 1), pub(market.M5, market.Buy, 4010, 0.4, 3, 9, 1), pub(market.M5, market.Sell, 4020, 0.4, 3, 2, 1)})
	if best == nil || best.levelPrice != 4020 {
		t.Fatalf("3 stars twice: the nearer level (4020, distance 2) wins: %+v", best)
	}
	if s.selectOne(nil) != nil {
		t.Fatal("nothing found, nothing selected")
	}
}

// A level found on a higher frame is known when the bar that completed it CLOSED: the reaction may not precede it, and an M5 level keeps its own frame's bar index.
func TestAHigherFrameLevelIsKnownAtTheCloseOfTheBarThatCompletedIt(t *testing.T) {
	const m5 = int64(300)
	m5Bars := make([]market.Candle, 40)
	for i := range m5Bars {
		m5Bars[i] = market.Candle{Time: 1_000_000 + int64(i)*m5}
	}
	m15Bars := []market.Candle{{Time: 1_000_000}, {Time: 1_000_900}, {Time: 1_001_800}, {Time: 1_002_700}, {Time: 1_003_600}}
	frame := &context.LegacyFrame{Bars: m15Bars, Swings: []techniquezone.Swing{{Price: 4000, ConfirmedIndex: 2}, {Price: 4000.2, ConfirmedIndex: 3}}}
	level := techniquezone.Level{Price: 4000, Band: 1}
	// minimum 2 touches: known when the 2nd swing was confirmed, on M15 bar 3 (open 1_002_700), which closes at 1_003_600: the first M5 bar at or after that is index 3600/300 = 12.
	if got := reactionKnownFrom(m5Bars, frame, market.M15, level, 2); got != 12 {
		t.Fatalf("M15 level known at the close of bar 3: first M5 bar %d, want 12", got)
	}
	// the same swings read as an M5 frame keep the frame's own bar index (v11 behaviour).
	m5Frame := &context.LegacyFrame{Bars: m5Bars, Swings: []techniquezone.Swing{{Price: 4000, ConfirmedIndex: 20}, {Price: 4000.2, ConfirmedIndex: 25}}}
	if got := reactionKnownFrom(m5Bars, m5Frame, market.M5, level, 2); got != 25 {
		t.Fatalf("an M5 level is known on its own bar 25, got %d", got)
	}
	// a level with no swing in its band has no formation to wait for.
	if got := reactionKnownFrom(m5Bars, &context.LegacyFrame{Bars: m15Bars}, market.M15, level, 2); got != 0 {
		t.Fatalf("no formation, no wait: %d", got)
	}
	// a level completed by the last M15 bar (open 1_003_600) is known at its close, 1_004_500: M5 bar 15; with M5 bars that end before that, none sees it yet.
	last := &context.LegacyFrame{Bars: m15Bars, Swings: []techniquezone.Swing{{Price: 4000, ConfirmedIndex: 4}}}
	if got := reactionKnownFrom(m5Bars, last, market.M15, level, 1); got != 15 {
		t.Fatalf("known at M5 bar 15: %d", got)
	}
	if got := reactionKnownFrom(m5Bars[:10], last, market.M15, level, 1); got != 10 {
		t.Fatalf("no M5 bar yet sees it: %d", got)
	}
}

func shippedParams() map[string]any {
	return map[string]any{
		"minimum_touches": 2, "invalidation_buffer_atr": 0.5, "fallback_target_r": 2.0, "expiry_hours": 24.0, "structure_close_buffer_atr": 0.0, "breakout_accept_bars": 2,
		"price_digits": 2, "detector_contract": "profit_week", "structure_timeframes": []any{"M5", "M15", "H1"}, "require_confirmation": true,
		"reaction_lookback_bars": 3, "engulfing_minimum_range_atr": 0.5, "require_candle_confirmation": false,
		// the instrument-scale and shared leaves the engine injects
		"pip_size": 0.1, "maximum_entry_atr": 2.0, "maximum_zone_width_atr": 3.0, "proximal_band_atr": 0.3, "fvg_entry_max_width_price": 5.0, "confluence_floor": 2,
		"confluence_star_three_ratio": 0.585, "confluence_star_two_ratio": 0.39, "confluence_zone_quality_weight": 4.0, "confluence_mad_score_weight": 2.0,
		"fibonacci_confluence_weight": 2.5, "fibonacci_epsilon_atr": 0.1, "confluence_scoring_version": "v1", "fibonacci_enabled": true,
	}
}

func TestKeyLevelParsesItsFramesAndRefusesM1(t *testing.T) {
	cfg, err := parseConfig(shippedParams())
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.StructureTimeframes) != 3 || cfg.StructureTimeframes[0] != market.M5 || cfg.StructureTimeframes[2] != market.H1 {
		t.Fatalf("frames %v", cfg.StructureTimeframes)
	}
	for name, mutate := range map[string]func(map[string]any){
		"M1":      func(p map[string]any) { p["structure_timeframes"] = []any{"M1", "M5"} },
		"H4":      func(p map[string]any) { p["structure_timeframes"] = []any{"H4"} },
		"missing": func(p map[string]any) { delete(p, "structure_timeframes") },
		"empty":   func(p map[string]any) { p["structure_timeframes"] = []any{} },
	} {
		p := shippedParams()
		mutate(p)
		if _, err := parseConfig(p); err == nil {
			t.Errorf("%s was accepted", name)
		}
	}
}

// require_confirmation: true makes a bare touch unpublishable even where the global switch is false; absent, the global decides (the touch is optional when it is false).
func TestKeyLevelOwnConfirmationRequirementOverridesTheGlobal(t *testing.T) {
	cfg, err := parseConfig(shippedParams())
	if err != nil || !cfg.RequireConfirmation || cfg.Detector.CandleConfirmationOptional {
		t.Fatalf("require_confirmation: true over a false global must require the reaction: %+v %v", cfg.RequireConfirmation, err)
	}
	p := shippedParams()
	delete(p, "require_confirmation")
	cfg, err = parseConfig(p)
	if err != nil || cfg.RequireConfirmation || !cfg.Detector.CandleConfirmationOptional {
		t.Fatalf("absent, the global false leaves the touch optional: %+v %v", cfg.RequireConfirmation, err)
	}
	p["require_candle_confirmation"] = true
	if cfg, _ = parseConfig(p); !cfg.RequireConfirmation || cfg.Detector.CandleConfirmationOptional {
		t.Fatal("absent, the global true requires the reaction")
	}
}
