package candle_test

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/candle"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func assertClose(t *testing.T, name string, got, want float64) {
	t.Helper()
	// Python's public telemetry representation rounds scores to four decimal
	// places; compare at that contract precision while retaining the full Go
	// calculation internally.
	if math.Abs(got-want) > 5e-5 {
		t.Fatalf("%s: got %.12f want %.12f", name, got, want)
	}
}

func TestPythonCandleConfirmationParityCases(t *testing.T) {
	tests := []struct {
		name       string
		bars       []market.Candle
		direction  string
		atr, level float64
		zoneLow    *float64
		zoneHigh   *float64
		base       float64
		final      float64
		primary    string
		patterns   []string
	}{
		{
			name: "sweep_indecision_displacement",
			bars: []market.Candle{
				{Open: 100, High: 101, Low: 99.5, Close: 100.1},
				{Open: 100.1, High: 100.4, Low: 99.9, Close: 100.2},
				{Open: 100, High: 102, Low: 100, Close: 101.8},
			},
			direction: "BUY", atr: .5, level: 100,
			zoneLow: ptr(100), zoneHigh: ptr(101),
			base: .794, final: .854, primary: "sweep_indecision_displacement",
			patterns: []string{"strong_close", "body_close", "engulfing", "strong_reclaim", "displacement_candle", "sweep_indecision_displacement"},
		},
		{
			name: "same_direction_engulfing_is_not_opposite",
			bars: []market.Candle{
				{Open: 100, High: 100.6, Low: 99.9, Close: 100.5},
				{Open: 100.2, High: 101.3, Low: 100.1, Close: 101.2},
			},
			direction: "BUY", atr: .5, level: 100,
			base: .8278, final: .8278, primary: "strong_reclaim",
			patterns: []string{"strong_close", "body_close", "strong_reclaim", "displacement_candle"},
		},
		{
			name: "evening_star_and_sweep",
			bars: []market.Candle{
				{Open: 101, High: 101.2, Low: 99.8, Close: 100},
				{Open: 100.1, High: 100.4, Low: 99.9, Close: 100.2},
				{Open: 100.3, High: 100.4, Low: 98.8, Close: 99},
			},
			direction: "SELL", atr: 1, level: 100,
			zoneLow: ptr(99), zoneHigh: ptr(101),
			base: .7398, final: .8598, primary: "sweep_reclaim",
			patterns: []string{"sweep_reclaim", "strong_close", "body_close", "engulfing", "strong_reclaim", "displacement_candle", "sweep_indecision_displacement"},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := candle.Evaluate(tc.bars, tc.direction, tc.atr, tc.level, tc.zoneLow, tc.zoneHigh)
			if got == nil {
				t.Fatal("expected evidence")
			}
			assertClose(t, "base_score", got.BaseScore, tc.base)
			assertClose(t, "final_score", got.FinalScore, tc.final)
			if got.PrimaryPattern != tc.primary {
				t.Fatalf("primary pattern: got %q want %q", got.PrimaryPattern, tc.primary)
			}
			if len(got.AllPatterns) != len(tc.patterns) {
				t.Fatalf("patterns: got %v want %v", got.AllPatterns, tc.patterns)
			}
			for i := range tc.patterns {
				if got.AllPatterns[i] != tc.patterns[i] {
					t.Fatalf("patterns: got %v want %v", got.AllPatterns, tc.patterns)
				}
			}
		})
	}
}

func TestCandleConfirmationConfigCanDisableFamilies(t *testing.T) {
	low, high := 100.0, 101.0
	bars := []market.Candle{
		{Open: 100, High: 101, Low: 99.5, Close: 100.1},
		{Open: 100.1, High: 100.4, Low: 99.9, Close: 100.2},
		{Open: 100, High: 102, Low: 100, Close: 101.8},
	}
	cfg := candle.DefaultConfig()
	cfg.Displacement.EngulfingEnabled = false
	cfg.Rejection.SweepEnabled = false
	cfg.Rejection.ReclaimEnabled = false
	got := candle.EvaluateWithConfig(bars, "BUY", .5, 100, &low, &high, cfg)
	if got == nil || got.Displacement == nil {
		t.Fatalf("expected remaining displacement evidence: %+v", got)
	}
	if got.Displacement.Engulfing || got.Rejection != nil {
		t.Fatalf("config switches were ignored: %+v", got)
	}
}

func ptr(v float64) *float64 { return &v }
