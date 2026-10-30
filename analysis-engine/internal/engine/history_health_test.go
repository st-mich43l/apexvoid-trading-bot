package engine

import (
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/state"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
)

func healthWorker(t *testing.T) *SymbolWorker {
	t.Helper()
	calendar, err := session.NewCalendar("Asia/Tokyo", 7, "Europe/London", 8, "America/New_York", 9, "America/New_York", 17)
	if err != nil {
		t.Fatal(err)
	}
	var settings Settings
	settings.PrimaryTimeframe = market.M5
	settings.Session = session.Config{Calendar: calendar}
	settings.LegacyRead.WindowBars = map[market.Timeframe]int{market.M5: 150, market.M15: 250}
	settings.Strategies = []strategy.Config{
		{ID: "crt", Enabled: true, Parameters: map[string]any{"atr_window_bars": 140.0, "reaction_lookback_bars": 3}},
		{ID: "ifvg", Enabled: false, Parameters: map[string]any{"target_lookback_bars": 900}},
	}
	return &SymbolWorker{state: &state.SymbolState{Symbol: "XAU"}, settings: settings, telemetry: telemetry.NewRecorder()}
}

func counts(w *SymbolWorker, name telemetry.Counter, tf market.Timeframe) int64 {
	var n int64
	_, samples := w.telemetry.Snapshot()
	for _, s := range samples {
		if s.Counter == string(name) && s.Timeframe == string(tf) {
			n += s.Count
		}
	}
	return n
}

func series(start int64, period int64, n int) []int64 {
	out := make([]int64, n)
	for i := range out {
		out[i] = start + int64(i)*period
	}
	return out
}

func TestShortHistoryIsCountedOnEveryEvaluationAndNeverBlocks(t *testing.T) {
	w := healthWorker(t)
	start := time.Date(2026, 10, 22, 0, 0, 0, 0, time.UTC).Unix()
	if got := requiredBars(w.settings, market.M5); got != 150 {
		t.Fatalf("required M5 bars %d, want max(window 150, enabled strategies' 140 and 3) = 150 (a disabled strategy's lookback does not count)", got)
	}
	var h historyHealth
	h.observe(w, market.M5, series(start, 300, 100))
	h.observe(w, market.M5, series(start, 300, 101))
	if n := counts(w, telemetry.CounterHistoryShort, market.M5); n != 2 {
		t.Fatalf("short history counted %d times, want 2", n)
	}
	h.observe(w, market.M5, series(start, 300, 150))
	if n := counts(w, telemetry.CounterHistoryShort, market.M5); n != 2 {
		t.Fatalf("a complete history is not counted: %d", n)
	}
}

func TestAnEnabledStrategyLongerLookbackRaisesTheRequirement(t *testing.T) {
	w := healthWorker(t)
	w.settings.Strategies[0].Parameters["atr_window_bars"] = 400.0
	if got := requiredBars(w.settings, market.M5); got != 400 {
		t.Fatalf("required M5 bars %d, want 400", got)
	}
	if got := requiredBars(w.settings, market.M15); got != 250 {
		t.Fatalf("a structure frame needs its window, got %d", got)
	}
}

func TestAGapIsCountedOnceAndForgottenWhenItLeavesTheWindow(t *testing.T) {
	w := healthWorker(t)
	start := time.Date(2026, 10, 22, 0, 0, 0, 0, time.UTC).Unix()
	whole := series(start, 300, 200)
	holed := append(append([]int64{}, whole[:120]...), whole[123:]...) // three bars missing
	var h historyHealth
	h.observe(w, market.M5, holed)
	h.observe(w, market.M5, holed)
	h.observe(w, market.M5, append(holed, holed[len(holed)-1]+300))
	if n := counts(w, telemetry.CounterHistoryGap, market.M5); n != 1 {
		t.Fatalf("one hole counted %d times, want once", n)
	}
	// 150 more bars push the hole out of the 150-bar window
	later := append([]int64{}, holed...)
	for i := 0; i < 160; i++ {
		later = append(later, later[len(later)-1]+300)
	}
	h.observe(w, market.M5, later)
	if n := counts(w, telemetry.CounterHistoryGap, market.M5); n != 1 {
		t.Fatalf("a hole outside the window is not counted again: %d", n)
	}
	if len(h.seen[market.M5]) != 0 {
		t.Fatalf("the forgotten hole is still tracked: %v", h.seen[market.M5])
	}
}
