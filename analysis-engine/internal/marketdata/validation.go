package marketdata

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// InvalidReason names why a candle failed ValidateCandle — machine-readable,
// matching the evidence.code discipline used elsewhere in this codebase
// (see opportunity.Evidence's doc comment) rather than only a free-text
// error string.
type InvalidReason string

const (
	InvalidNonPositiveTime  InvalidReason = "non_positive_time"
	InvalidNonFiniteOHLC    InvalidReason = "non_finite_ohlc"
	InvalidNegativeVolume   InvalidReason = "negative_volume"
	InvalidHighBelowLow     InvalidReason = "high_below_low"
	InvalidHighBelowOpen    InvalidReason = "high_below_open"
	InvalidHighBelowClose   InvalidReason = "high_below_close"
	InvalidLowAboveOpen     InvalidReason = "low_above_open"
	InvalidLowAboveClose    InvalidReason = "low_above_close"
	InvalidUnknownTimeframe InvalidReason = "unknown_timeframe"
)

// ValidationError reports exactly why a candle was rejected — never a bare
// bool, per docs/analysis/market-structure-v2.md's validation section
// ("reject or explicitly classify malformed candles").
type ValidationError struct {
	Reason InvalidReason
	Candle market.Candle
}

func (e *ValidationError) Error() string {
	return "marketdata: invalid candle (" + string(e.Reason) + ")"
}

// ValidateCandle rejects NaN/Inf, malformed OHLC (High < Low, or either
// extreme not actually extreme relative to Open/Close), negative volume,
// and a non-positive timestamp — source task §9's exact checklist. A
// zero-range candle (High == Low == Open == Close) is valid; it is real
// on ultra-quiet ticks and a flat market is a market state to represent,
// not to reject (see market.Candle.Range()'s own doc comment on this same
// point, and the indicator package's flat-market test cases).
func ValidateCandle(c market.Candle) error {
	if c.Time <= 0 {
		return &ValidationError{Reason: InvalidNonPositiveTime, Candle: c}
	}
	for _, v := range [...]float64{c.Open, c.High, c.Low, c.Close, c.Volume} {
		if !market.Price(v).IsFinite() {
			return &ValidationError{Reason: InvalidNonFiniteOHLC, Candle: c}
		}
	}
	if c.Volume < 0 {
		return &ValidationError{Reason: InvalidNegativeVolume, Candle: c}
	}
	if c.High < c.Low {
		return &ValidationError{Reason: InvalidHighBelowLow, Candle: c}
	}
	if c.High < c.Open {
		return &ValidationError{Reason: InvalidHighBelowOpen, Candle: c}
	}
	if c.High < c.Close {
		return &ValidationError{Reason: InvalidHighBelowClose, Candle: c}
	}
	if c.Low > c.Open {
		return &ValidationError{Reason: InvalidLowAboveOpen, Candle: c}
	}
	if c.Low > c.Close {
		return &ValidationError{Reason: InvalidLowAboveClose, Candle: c}
	}
	return nil
}

// ValidateTimeframe fails closed on an instrument/timeframe pair the feed
// should never produce — see market.ParseTimeframe's own doc comment for
// why this must never silently default rather than reject (§12 of the
// architecture task, restated for marketdata's own boundary).
func ValidateTimeframe(tf market.Timeframe) error {
	if _, ok := tf.Minutes(); !ok {
		return &ValidationError{Reason: InvalidUnknownTimeframe}
	}
	return nil
}
