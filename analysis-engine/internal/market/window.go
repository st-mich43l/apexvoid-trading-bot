package market

// CandleWindow is a bounded, oldest-first ring buffer of closed candles for
// one symbol+timeframe. It mirrors the role of a pandas DataFrame window in
// the Python engine (app/analysis/ohlc_source.py's `window_for_timeframe` /
// the `frames[tf]` dict `_analyze_tf` receives) without pulling in Pandas
// semantics — see rebuild-analysis-engine.md §8.
//
// Correctness first (§9): this is a plain bounded buffer with an O(n)
// Snapshot, not yet an incremental/streaming structure. Algorithms in
// package indicator/structure operate on the Snapshot() slice today, the
// same way their Python originals operate on a full df window; making any
// individual one incremental is a deliberate, separate, later change.
type CandleWindow struct {
	buf      []Candle
	capacity int
	start    int // index of the oldest element within buf
	size     int // number of valid elements
}

// NewCandleWindow creates a window that retains at most capacity candles.
// capacity <= 0 panics — a window with no bound defeats the point of using
// one (§8) and every caller should know its own required lookback.
func NewCandleWindow(capacity int) *CandleWindow {
	if capacity <= 0 {
		panic("market: CandleWindow capacity must be positive")
	}
	return &CandleWindow{
		buf:      make([]Candle, capacity),
		capacity: capacity,
	}
}

// Push appends a new closed candle, evicting the oldest one once the window
// is at capacity. Candles must be pushed in strictly increasing Time order;
// out-of-order/duplicate delivery is the caller's (per-symbol dispatcher's)
// responsibility to prevent — see §6 on preserving per-symbol FIFO order.
func (w *CandleWindow) Push(c Candle) {
	idx := (w.start + w.size) % w.capacity
	w.buf[idx] = c
	if w.size < w.capacity {
		w.size++
		return
	}
	// Full: the write above already overwrote the old oldest slot: advance
	// start to the new oldest.
	w.start = (w.start + 1) % w.capacity
}

// Len returns the number of candles currently held (<= capacity).
func (w *CandleWindow) Len() int { return w.size }

// Capacity returns the configured maximum window size.
func (w *CandleWindow) Capacity() int { return w.capacity }

// At returns the i-th candle, oldest-first (0 == oldest, Len()-1 == newest),
// matching pandas' positional `df.iloc[i]` ordering used throughout the
// Python engine. Panics on an out-of-range index — callers already bounds
// check against Len() the same way Python code checks `len(df)`.
func (w *CandleWindow) At(i int) Candle {
	if i < 0 || i >= w.size {
		panic("market: CandleWindow index out of range")
	}
	return w.buf[(w.start+i)%w.capacity]
}

// Last returns the newest candle. Panics if the window is empty.
func (w *CandleWindow) Last() Candle {
	return w.At(w.size - 1)
}

// ReplaceLast overwrites the newest candle in place, without changing Len()
// or evicting anything. Added for internal/marketdata.TimeframeHistory's
// same-timestamp replacement policy (a live-updating forming bar overwrites
// its own not-yet-closed slot rather than growing the window) — see
// docs/analysis/market-structure-v2.md's causality section. Panics if the
// window is empty, same discipline as Last().
func (w *CandleWindow) ReplaceLast(c Candle) {
	if w.size == 0 {
		panic("market: ReplaceLast on an empty CandleWindow")
	}
	w.buf[(w.start+w.size-1)%w.capacity] = c
}

// Snapshot materializes the window as a plain oldest-first slice, the Go
// analogue of handing a function the full `df`. Allocates; algorithms that
// need this every bar are exactly the ones §9 flags as later incremental
// candidates (ATR chief among them).
func (w *CandleWindow) Snapshot() []Candle {
	out := make([]Candle, w.size)
	for i := 0; i < w.size; i++ {
		out[i] = w.buf[(w.start+i)%w.capacity]
	}
	return out
}
