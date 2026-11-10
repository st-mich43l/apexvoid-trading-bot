package rangescalp

import (
	"math"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

func testStrategy(t *testing.T) *Strategy {
	t.Helper()
	params := map[string]any{
		"pip_size": 0.1, "stop_minimum_pips": 30.0, "stop_maximum_pips": 40.0, "minimum_net_target_pips": 10.0,
		"maximum_efficiency_ratio": 0.5, "minimum_range_atr": 1.0, "maximum_range_atr": 6.0, "edge_fraction": 0.25, "stop_buffer_atr": 0.0,
		"entry_width_pips": 4.0, "expiry_minutes": 30.0, "range_lookback_bars": 12, "cooldown_bars": 6, "maximum_signals_per_day": 3,
		"atr_length": 14, "atr_window_bars": 140, "trading_day_rollover_utc_hour": 22, "symbols": []any{"XAU"},
		"reward_risk_ladder": []any{2.0, 1.0},
	}
	s, err := New(strategy.Config{ID: ID, Version: Version, Parameters: params})
	if err != nil {
		t.Fatal(err)
	}
	return s.(*Strategy)
}

// signalFor builds a probe whose structural stop, from the worst planned entry to the invalidation, is exactly the given pips.
func signalFor(direction market.Direction, structuralPips float64, cfg Config) signal {
	pip := cfg.PipSize
	half := cfg.EntryWidthPips * pip / 2
	close := 4000.0
	sig := signal{Direction: direction, Index: 0, ATR: 1, Close: close}
	if direction == market.Sell {
		worst := close - half
		sig.SignalHigh = worst + structuralPips*pip
		sig.High = sig.SignalHigh
	} else {
		worst := close + half
		sig.SignalLow = worst - structuralPips*pip
		sig.Low = sig.SignalLow
	}
	return sig
}

func TestRangeScalpStopCap(t *testing.T) {
	s := testStrategy(t)
	ctx := &analysiscontext.MarketContext{Symbol: "XAU"}
	bars := []market.Candle{{Time: 1_700_000_000}}
	cases := []struct {
		pips float64
		want float64 // published stop, 0 = refused
	}{{24, 30}, {35, 35}, {40, 40}, {40.5, 0}, {48, 0}}
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		for _, c := range cases {
			candidate, ok := s.candidate(ctx, bars, signalFor(direction, c.pips, s.cfg))
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
			// The distance from the WORST entry (the zone edge furthest from the stop) to the invalidation.
			worst := float64(candidate.Entry.High)
			invalidation := float64(candidate.Invalidation.Price)
			if direction == market.Sell {
				worst = float64(candidate.Entry.Low)
			}
			got := math.Abs(invalidation-worst) / s.cfg.PipSize
			if math.Abs(got-c.want) > 1e-6 || got > 40+1e-6 {
				t.Errorf("%s %.1f pips: published stop %.6f pips, want %.0f and never above 40", direction, c.pips, got, c.want)
			}
		}
	}
}
