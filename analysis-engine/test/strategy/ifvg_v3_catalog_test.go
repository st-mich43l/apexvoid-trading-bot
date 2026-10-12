package strategy_test

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/ifvg"
)

// ifvgParams is the iFVG v3 parameter set (config/analysis.yml, XAU scale).
func ifvgParams() map[string]any {
	return detectorParams(map[string]any{
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
		"entry_max_width_price": 5.0,
	})
}

// ifvgBars is a BUY iFVG on an XAU-scale M5 path: an opposing swing high, a bearish
// gap 4112..gapTop, a strong close above it, a retest touching its top and a
// bullish rejection.
func ifvgBars(gapTop float64) []market.Candle {
	var bars []market.Candle
	t, last := int64(1_760_000_400), 4110.0
	raw := func(o, h, l, c float64) {
		bars = append(bars, market.Candle{Time: t, Open: o, High: h, Low: l, Close: c, Volume: 1})
		last, t = c, t+300
	}
	step := func(c, up, dn float64) { raw(last, math.Max(last, c)+up, math.Min(last, c)-dn, c) }
	for i := 0; i < 40; i++ {
		if i%2 == 0 {
			step(4110.3, 0.7, 0.7)
		} else {
			step(4109.7, 0.7, 0.7)
		}
	}
	step(4110.6, 0.5, 0.5)
	step(4111, 20, 0.5)
	step(4110.6, 0.5, 0.5)
	step(4110.2, 0.5, 0.5)
	for i := 0; i < 25; i++ {
		if i%2 == 0 {
			step(4110.3, 0.7, 0.7)
		} else {
			step(4109.7, 0.7, 0.7)
		}
	}
	raw(4118.0, 4118.4, gapTop, gapTop+0.4)
	raw(4116.4, 4116.6, 4110.2, 4110.8)
	raw(4110.8, 4112.0, 4108.0, 4109.0)
	for i := 0; i < 3; i++ {
		step(4110.5+0.3*float64(i%2), 0.6, 0.6)
	}
	raw(4111.0, gapTop+2.3, 4110.8, gapTop+2.0)
	raw(gapTop+2.4, gapTop+2.6, gapTop+0.2, gapTop+1.6)
	raw(gapTop+0.8, gapTop+2.4, gapTop-0.6, gapTop+2.2)
	return bars
}

func ifvgContext(bars []market.Candle) *analysiscontext.MarketContext {
	return ctx(map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: bars}})
}

func TestIFVGV3KnownQualifyingFixture(t *testing.T) {
	s, err := ifvg.New(strategy.Config{ID: ifvg.ID, Version: ifvg.Version, Enabled: true, Parameters: ifvgParams()})
	if err != nil {
		t.Fatal(err)
	}
	assertOne(t, s, ifvgContext(ifvgBars(4116.0)))
}

// A repeated confirmation of the same retest is the same opportunity, not a second one.
func TestIFVGV3RepeatedEvaluationEmitsOneCandidate(t *testing.T) {
	s, err := ifvg.New(strategy.Config{ID: ifvg.ID, Version: ifvg.Version, Enabled: true, Parameters: ifvgParams()})
	if err != nil {
		t.Fatal(err)
	}
	bars := ifvgBars(4116.0)
	first := s.Evaluate(ifvgContext(bars))
	extended := append(append([]market.Candle(nil), bars...), market.Candle{Time: bars[len(bars)-1].Time + 300, Open: 4118.2, High: 4118.6, Low: 4117.9, Close: 4118.4, Volume: 1})
	again := s.Evaluate(ifvgContext(extended))
	if len(first) != 1 || len(again) != 1 || first[0].ID != again[0].ID {
		t.Fatalf("one retest, one opportunity: %d then %d", len(first), len(again))
	}
}

// Production 2026-09-30: XAU traded a 0.10 ATR inverted gap that stopped out inside
// a minute. A sliver gap is not a fair-value gap at all.
func TestIFVGV3IgnoresAGapThatIsASliverOfATR(t *testing.T) {
	s, err := ifvg.New(strategy.Config{ID: ifvg.ID, Version: ifvg.Version, Enabled: true, Parameters: ifvgParams()})
	if err != nil {
		t.Fatal(err)
	}
	if got := s.Evaluate(ifvgContext(ifvgBars(4112.4))); len(got) != 0 {
		t.Fatalf("a 0.4 point gap (about 0.13 ATR) must not produce a candidate, got %d", len(got))
	}
	if got := s.Evaluate(ifvgContext(ifvgBars(4116.0))); len(got) != 1 {
		t.Fatalf("a 4.0 point gap must produce a candidate, got %d", len(got))
	}
}
