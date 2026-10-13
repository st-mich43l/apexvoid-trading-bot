package confluencezone

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const (
	t0     = int64(1_000_000) // open time of bar 0; one M5 bar is 300 s
	atrXAU = 5.0              // an XAU-scale M5 ATR (50 pips)
	// factsReady is before every bar: the structures were already confirmable.
	factsReady = int64(500_000)
)

func testParams(extra map[string]any) map[string]any {
	params := map[string]any{
		"minimum_facts": 2.0, "minimum_overlap_pips": 1.0, "minimum_overlap_atr": 0.05,
		"reaction_lookback_bars": 3.0, "minimum_sweep_pips": 1.0, "minimum_sweep_atr": 0.05, "minimum_reclaim_atr": 0.0,
		"rejection_wick_ratio": 0.25, "rejection_close_strength": 0.6, "displacement_body_ratio": 0.6,
		"displacement_range_atr": 0.5, "displacement_grade_atr": 1.0, "invalidation_buffer_atr": 0.5,
		"minimum_target_distance_atr": 1.0, "minimum_reward_risk": 1.15, "expiry_hours": 12.0,
		"pip_size": 0.1, "maximum_entry_atr": 2.0, "fvg_entry_max_width_price": 5.0,
	}
	for k, v := range extra {
		params[k] = v
	}
	return params
}

func testConfig(t *testing.T, extra map[string]any) Config {
	t.Helper()
	cfg, err := parseConfig(testParams(extra))
	if err != nil {
		t.Fatal(err)
	}
	return cfg
}

func bar(i int, o, h, l, c float64) market.Candle {
	return market.Candle{Time: t0 + int64(i)*300, Open: o, High: h, Low: l, Close: c, Volume: 1}
}

// approach is 30 closed candles drifting down to 4106 above a demand region at
// 4103-4105, so the last candle can be the touch and the rejection. The final
// candle is replaced by the caller.
func approach() []market.Candle {
	bars := make([]market.Candle, 30)
	for i := range bars {
		bars[i] = bar(i, 4110, 4110.5, 4109.5, 4110)
	}
	bars[27] = bar(27, 4109, 4109.5, 4106.5, 4107)
	bars[28] = bar(28, 4107, 4107.2, 4105.5, 4106)
	return bars
}

// withLast returns the approach with the final candle set.
func withLast(o, h, l, c float64) []market.Candle {
	bars := approach()
	bars[29] = bar(29, o, h, l, c)
	return bars
}

// rejection is a candle that sweeps below the region's low (4103) and closes
// back inside and above it: a long lower wick, a close near the high.
func rejection() []market.Candle { return withLast(4104.5, 4106, 4102, 4105.5) }

func fact(id, side string, low, high float64, avail int64, atoms ...string) structuralconfluence.Fact {
	return structuralconfluence.Fact{
		ID: id, Source: structuralconfluence.SourceSupplyDemand, Side: side, Low: low, High: high, StructuralLow: low, StructuralHigh: high,
		FormedAt: avail - 1000, AvailableAt: avail, Atoms: atoms, Verified: true, Score: 9.5,
	}
}

// twoDemand are two independent demand structures A 4100-4105 and B 4103-4108:
// the supported region is 4103-4105.
func twoDemand() []structuralconfluence.Fact {
	return []structuralconfluence.Fact{
		fact("A", structuralconfluence.Buy, 4100, 4105, factsReady, "leg:a"),
		fact("B", structuralconfluence.Buy, 4103, 4108, factsReady, "gap:b"),
	}
}

func buyPool(level float64) techniquezone.Pool { return techniquezone.Pool{Side: "buy", Level: level} }

func input(bars []market.Candle, facts []structuralconfluence.Fact, pools ...techniquezone.Pool) Input {
	return Input{Bars: bars, ATR: atrXAU, PeriodSeconds: 300, Facts: facts, Pools: pools}
}

func only(t *testing.T, a Analysis) Setup {
	t.Helper()
	if len(a.Setups) != 1 {
		t.Fatalf("want exactly one setup, got %d; rejections: %+v", len(a.Setups), a.Rejections)
	}
	return a.Setups[0]
}

func hasReason(a Analysis, reason string) bool {
	for _, r := range a.Rejections {
		if r.Reason == reason {
			return true
		}
	}
	return false
}

func poolSell(level float64) techniquezone.Pool {
	return techniquezone.Pool{Side: "sell", Level: level}
}
