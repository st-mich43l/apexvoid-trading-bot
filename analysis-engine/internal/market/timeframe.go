package market

import "fmt"

// Timeframe is one of the fixed candle intervals the feed produces. A typed
// enum instead of a bare string keeps an unrecognized timeframe a compile-
// or parse-time error rather than a silent map-miss deep in an indicator.
type Timeframe string

const (
	M1  Timeframe = "M1"
	M3  Timeframe = "M3"
	M5  Timeframe = "M5"
	M15 Timeframe = "M15"
	M30 Timeframe = "M30"
	H1  Timeframe = "H1"
	H4  Timeframe = "H4"
	D1  Timeframe = "D1"
)

// minutesByTimeframe mirrors app/analysis/engine.py's `_TF_MINUTES` exactly
// — do not add a timeframe here that Python's map doesn't also have, and
// vice versa (§17: preserve behavior, don't quietly extend it).
var minutesByTimeframe = map[Timeframe]int{
	M1:  1,
	M3:  3,
	M5:  5,
	M15: 15,
	M30: 30,
	H1:  60,
	H4:  240,
	D1:  1440,
}

// Minutes returns the timeframe's bar length in minutes and whether it was
// recognized. An unrecognized timeframe must fail closed at the caller —
// see §12 — never silently default to some minute count.
func (tf Timeframe) Minutes() (int, bool) {
	m, ok := minutesByTimeframe[tf]
	return m, ok
}

// ParseTimeframe normalizes and validates a raw string (as it arrives off
// the `bars:new` / `bars:{SYMBOL}:{TF}` Redis contract, see
// app/analysis/ohlc_source.py) into a known Timeframe. Returns an error for
// anything unrecognized instead of coercing it — an unknown timeframe must
// fail closed (§12), never fall back to a guess.
func ParseTimeframe(raw string) (Timeframe, error) {
	tf := Timeframe(raw)
	if _, ok := minutesByTimeframe[tf]; !ok {
		return "", fmt.Errorf("market: unrecognized timeframe %q", raw)
	}
	return tf, nil
}
