package impulseretrace

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// validParams is a complete Impulse Retrace configuration (the engine injects the pip size, the scalp book and the barrier policy).
func validParams() map[string]any {
	return map[string]any{
		"pip_size": 0.1, "stop_minimum_pips": 30.0, "stop_maximum_pips": 40.0, "minimum_net_target_pips": 10.0, "impulse_body_atr": 1.5,
		"retrace_fraction": 0.5, "stop_buffer_atr": 0.0, "entry_width_pips": 4.0, "entry_window_minutes": 30.0,
		"cooldown_bars": 6, "atr_length": 14, "atr_window_bars": 140, "symbols": []any{"XAU"}, "reward_risk_ladder": []any{2.0, 1.0},
		"room_projection": 1.0, "barrier_buffer_atr": 0.15, "barrier_max_width_atr": 2.0, "barrier_max_width_pips": 100.0,
	}
}

func testStrategy(t *testing.T) *Strategy {
	t.Helper()
	params := validParams()
	s, err := New(strategy.Config{ID: ID, Version: Version, Parameters: params})
	if err != nil {
		t.Fatal(err)
	}
	return s.(*Strategy)
}

// impulseBar is a displacement bar whose structural stop, from the worst planned entry to the impulse origin, is exactly the given pips.
func impulseBar(direction market.Direction, structuralPips float64, cfg Config) market.Candle {
	pip := cfg.PipSize
	span := (structuralPips*pip - cfg.EntryWidthPips*pip) / (1 - cfg.RetraceFraction)
	if direction == market.Buy {
		return market.Candle{Time: 1_700_000_000, Open: 4000, Low: 4000, High: 4000 + span, Close: 4000 + span}
	}
	return market.Candle{Time: 1_700_000_000, Open: 4000 + span, High: 4000 + span, Low: 4000, Close: 4000}
}

func TestImpulseRetraceStopCap(t *testing.T) {
	s := testStrategy(t)
	ctx := &analysiscontext.MarketContext{Symbol: "XAU"}
	cases := []struct {
		pips float64
		want float64 // published stop, 0 = refused
	}{{24, 30}, {35, 35}, {40, 40}, {40.5, 0}, {48, 0}}
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		for _, c := range cases {
			candidate, reason := s.candidate(ctx, "", impulseBar(direction, c.pips, s.cfg), direction, 1)
			ok := reason == ""
			if c.want == 0 {
				if ok {
					t.Errorf("%s %.1f pips: published, want refused (a wider structural stop is never compressed)", direction, c.pips)
				}
				continue
			}
			if !ok {
				t.Errorf("%s %.1f pips: refused, want a %.0f-pip stop", direction, c.pips, c.want)
				continue
			}
			worst := float64(candidate.Entry.High)
			if direction == market.Sell {
				worst = float64(candidate.Entry.Low)
			}
			got := math.Abs(float64(candidate.Invalidation.Price)-worst) / s.cfg.PipSize
			if math.Abs(got-c.want) > 1e-6 || got > 40+1e-6 {
				t.Errorf("%s %.1f pips: published stop %.6f pips, want %.0f and never above 40", direction, c.pips, got, c.want)
			}
		}
	}
}
