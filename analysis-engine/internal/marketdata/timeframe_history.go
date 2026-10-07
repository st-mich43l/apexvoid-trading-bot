package marketdata

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// AppendResult is the explicit outcome of one TimeframeHistory.Append call
// — source task §8: "do NOT silently accept malformed sequencing."
type AppendResult int

const (
	AppendAccepted AppendResult = iota
	AppendDuplicate
	AppendOutOfOrder
	AppendReplaced
	AppendRejectedInvalid
	AppendConflict
)

// TimeframeHistory is the bounded, causally-sequenced candle history for
// one symbol+timeframe — the concrete type MarketHistory.Timeframes holds
// one of per configured timeframe.
// Wraps market.CandleWindow (bounded ring buffer) with the sequencing/
// validation contract §8-9 require: a duplicate timestamp is reported, not
// silently ignored or silently pushed as a new bar; an out-of-order
// timestamp is rejected, never accepted as if it were causal; malformed
// OHLC never reaches the window at all.
type TimeframeHistory struct {
	symbol       market.Symbol
	timeframe    market.Timeframe
	window       *market.CandleWindow
	allowReplace bool // replace-in-place policy for a same-timestamp candle (a live-updating forming bar) — see Append's doc comment
}

// NewTimeframeHistory returns an empty history bounded to depth candles.
// allowReplace controls what happens when Append receives a candle whose
// Time equals the current newest candle's Time: true replaces it in place
// (the live-updating-forming-bar case), false reports AppendDuplicate and
// leaves the window untouched (the closed-bar-only feed case). depth <= 0
// panics — mirrors market.NewCandleWindow's own fail-closed contract.
func NewTimeframeHistory(symbol market.Symbol, timeframe market.Timeframe, depth int, allowReplace bool) *TimeframeHistory {
	return &TimeframeHistory{
		symbol:       symbol,
		timeframe:    timeframe,
		window:       market.NewCandleWindow(depth),
		allowReplace: allowReplace,
	}
}

// Append validates c and applies the sequencing policy documented on
// NewTimeframeHistory. Returns the concrete outcome plus an error only for
// AppendRejectedInvalid (malformed OHLC/non-finite/non-positive time) —
// every other outcome is a valid, expected control-flow result, not an
// error, matching source task §8's explicit-outcome requirement.
func (h *TimeframeHistory) Append(c market.Candle) (AppendResult, error) {
	if err := ValidateCandle(c); err != nil {
		return AppendRejectedInvalid, err
	}
	if h.window.Len() == 0 {
		h.window.Push(c)
		return AppendAccepted, nil
	}
	last := h.window.Last()
	switch {
	case c.Time > last.Time:
		h.window.Push(c)
		return AppendAccepted, nil
	case c.Time == last.Time:
		if h.allowReplace {
			h.window.ReplaceLast(c)
			return AppendReplaced, nil
		}
		// Redis recovery and any future replay can legitimately deliver the
		// same closed-bar identity again. Same identity + identical payload
		// is an ordinary, safe-to-ignore duplicate; same identity + a
		// DIFFERENT payload is a conflict/correction (source task §17/§54)
		// and must never be silently folded into the same outcome — a
		// retroactive, silent OHLC change here would rewrite the bar
		// underneath structure/liquidity state already computed from the
		// original values, a causality violation. market.Candle is a plain
		// comparable struct, so a direct == is an exact-payload check.
		if c == last {
			return AppendDuplicate, nil
		}
		return AppendConflict, nil
	default: // c.Time < last.Time
		// Recovery can legitimately re-read any retained timestamp after a
		// reconnect or a Redis notification for a corrected score. Preserve
		// the explicit duplicate/conflict distinction even when that candle
		// is no longer the newest one; a timestamp absent from the retained
		// window remains a true out-of-order event.
		if existing, ok := h.At(c.Time); ok {
			if existing == c {
				return AppendDuplicate, nil
			}
			return AppendConflict, nil
		}
		return AppendOutOfOrder, nil
	}
}

// Len, Capacity: see market.CandleWindow — same semantics, this type is a
// thin, policy-enforcing wrapper over it, not a reimplementation.
func (h *TimeframeHistory) Len() int { return h.window.Len() }

// Newest returns the most recently appended (or replaced) candle. Panics
// if empty.
func (h *TimeframeHistory) Newest() market.Candle { return h.window.Last() }

// Snapshot returns every retained candle, oldest-first. Structure/liquidity
// algorithms take a narrower LatestN slice for their actual calculation
// window (source task §6: stored history != calculation lookback) — this
// full snapshot exists for callers that genuinely need the whole retained
// window (e.g. visualization, a full-history research replay).
func (h *TimeframeHistory) Snapshot() []market.Candle { return h.window.Snapshot() }

// At returns the candle whose Time equals t, if retained. Append's
// strictly-increasing-Time discipline (OutOfOrder is rejected, a same-
// timestamp Duplicate/Replace never creates a second entry) means the
// retained window is always sorted by Time, so this is a binary search,
// not a linear scan.
func (h *TimeframeHistory) At(t int64) (market.Candle, bool) {
	full := h.window.Snapshot() // O(n); see market.CandleWindow.Snapshot's own doc comment on why this isn't yet incremental
	lo, hi := 0, len(full)
	for lo < hi {
		mid := (lo + hi) / 2
		if full[mid].Time < t {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	if lo < len(full) && full[lo].Time == t {
		return full[lo], true
	}
	return market.Candle{}, false
}
