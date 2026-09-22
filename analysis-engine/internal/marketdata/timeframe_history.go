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

func (r AppendResult) String() string {
	switch r {
	case AppendAccepted:
		return "accepted"
	case AppendDuplicate:
		return "duplicate"
	case AppendOutOfOrder:
		return "out_of_order"
	case AppendReplaced:
		return "replaced"
	case AppendRejectedInvalid:
		return "rejected_invalid"
	case AppendConflict:
		return "conflict"
	default:
		return "unknown"
	}
}

// TimeframeHistory is the bounded, causally-sequenced candle history for
// one symbol+timeframe — the concrete type MarketHistory.Timeframes holds
// one of per configured timeframe (docs/analysis/market-structure-v2.md).
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

// Symbol and Timeframe identify which series this history holds.
func (h *TimeframeHistory) Symbol() market.Symbol       { return h.symbol }
func (h *TimeframeHistory) Timeframe() market.Timeframe { return h.timeframe }

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
		// At-least-once delivery (Kafka transport, docs/transport/kafka.md's
		// "Duplicate delivery handling") means the same closed-bar identity
		// can legitimately arrive twice. Same identity + identical payload
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
		return AppendOutOfOrder, nil
	}
}

// Len, Capacity: see market.CandleWindow — same semantics, this type is a
// thin, policy-enforcing wrapper over it, not a reimplementation.
func (h *TimeframeHistory) Len() int      { return h.window.Len() }
func (h *TimeframeHistory) Capacity() int { return h.window.Capacity() }

// Oldest returns the earliest retained candle. Panics if empty, matching
// market.CandleWindow.At's own contract (callers already check Len()).
func (h *TimeframeHistory) Oldest() market.Candle { return h.window.At(0) }

// Newest returns the most recently appended (or replaced) candle. Panics
// if empty.
func (h *TimeframeHistory) Newest() market.Candle { return h.window.Last() }

// Snapshot returns every retained candle, oldest-first. Structure/liquidity
// algorithms take a narrower LatestN slice for their actual calculation
// window (source task §6: stored history != calculation lookback) — this
// full snapshot exists for callers that genuinely need the whole retained
// window (e.g. visualization, a full-history research replay).
func (h *TimeframeHistory) Snapshot() []market.Candle { return h.window.Snapshot() }

// LatestN returns the newest min(n, Len()) candles, oldest-first — the
// narrow calculation-window read every structure/liquidity/indicator call
// should use instead of Snapshot's full stored depth (§6).
func (h *TimeframeHistory) LatestN(n int) []market.Candle {
	if n <= 0 {
		return nil
	}
	full := h.window.Snapshot()
	if n >= len(full) {
		return full
	}
	return full[len(full)-n:]
}

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

// Range returns every retained candle with fromTime <= Time <= toTime,
// oldest-first.
func (h *TimeframeHistory) Range(fromTime, toTime int64) []market.Candle {
	full := h.window.Snapshot()
	start := 0
	for start < len(full) && full[start].Time < fromTime {
		start++
	}
	end := start
	for end < len(full) && full[end].Time <= toTime {
		end++
	}
	return full[start:end]
}
