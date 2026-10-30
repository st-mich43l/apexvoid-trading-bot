package engine

import (
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
)

// historyHealth watches the candle history a frame is analysed on. It only counts and logs: a short or holed history never blocks an evaluation (the
// counters tell an operator that a replay or a feed reconnect left the analysis with less than it believes it has).
type historyHealth struct {
	short map[market.Timeframe]bool
	seen  map[market.Timeframe]map[int64]bool // gap starts already counted
}

// requiredBars is the number of closed bars the frame needs: its detection window, and on the execution frame the largest lookback or window any enabled
// strategy declares (atr_window_bars, target_lookback_bars, ...).
func requiredBars(settings Settings, tf market.Timeframe) int {
	need := settings.LegacyRead.WindowBars[tf]
	if tf != settings.PrimaryTimeframe {
		return need
	}
	for _, cfg := range settings.Strategies {
		if !cfg.Enabled {
			continue
		}
		for key, value := range cfg.Parameters {
			if !strings.HasSuffix(key, "_window_bars") && !strings.HasSuffix(key, "_lookback_bars") {
				continue
			}
			switch n := value.(type) {
			case int:
				need = maxInt(need, n)
			case float64:
				need = maxInt(need, int(n))
			}
		}
	}
	return need
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// observe checks the frame that just closed: tf's in-memory candles against what the frame needs, and the series inside its window for holes.
func (h *historyHealth) observe(w *SymbolWorker, tf market.Timeframe, times []int64) {
	window := w.settings.LegacyRead.WindowBars[tf]
	if window <= 0 {
		return
	}
	symbol, label := string(w.state.Symbol), string(tf)
	need := requiredBars(w.settings, tf)
	if h.short == nil {
		h.short = map[market.Timeframe]bool{}
		h.seen = map[market.Timeframe]map[int64]bool{}
	}
	isShort := len(times) < need
	if isShort {
		w.telemetry.Count(telemetry.CounterHistoryShort, symbol, label, 1)
	}
	if isShort != h.short[tf] {
		h.short[tf] = isShort
		if isShort {
			log.Warn("analysis history is short", "symbol", symbol, "timeframe", label, "bars", len(times), "need", need)
		} else {
			log.Info("analysis history is complete again", "symbol", symbol, "timeframe", label, "bars", len(times), "need", need)
		}
	}
	minutes, ok := tf.Minutes()
	if !ok {
		return
	}
	inWindow := times
	if len(inWindow) > window {
		inWindow = inWindow[len(inWindow)-window:]
	}
	seen := h.seen[tf]
	if seen == nil {
		seen = map[int64]bool{}
		h.seen[tf] = seen
	}
	current := map[int64]bool{}
	for _, start := range w.settings.Session.Gaps(inWindow, int64(minutes)*60) {
		current[start] = true
		if !seen[start] {
			seen[start] = true
			w.telemetry.Count(telemetry.CounterHistoryGap, symbol, label, 1)
			log.Warn("analysis history has a gap", "symbol", symbol, "timeframe", label, "first_missing_bar", start)
		}
	}
	for start := range seen { // a gap that left the window can be forgotten
		if !current[start] {
			delete(seen, start)
		}
	}
}
