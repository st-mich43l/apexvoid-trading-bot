// Package scribe writes closed M5 candles from close-to-close waypoints so strategy fixtures read as price paths with explicit wicks instead of raw
// OHLC tuples. It is test support only.
package scribe

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Scribe builds an M5 series: each candle opens at the previous close and carries explicit wicks.
type Scribe struct {
	t    int64
	last float64
	Bars []market.Candle
}

// New starts a series whose first candle opens at firstOpen (Unix seconds) at the given price.
func New(firstOpen int64, price float64) *Scribe { return &Scribe{t: firstOpen, last: price} }

// Next is the open time of the next candle.
func (s *Scribe) Next() int64 { return s.t }

// Last is the last close.
func (s *Scribe) Last() float64 { return s.last }

// Index is the index of the candle the next Bar call will write.
func (s *Scribe) Index() int { return len(s.Bars) }

// Bar appends a candle that opens at the previous close, closes at close, and has upper/lower wicks beyond its body.
func (s *Scribe) Bar(close, upper, lower float64) {
	open := s.last
	s.Bars = append(s.Bars, market.Candle{Time: s.t, Open: open, Close: close, High: math.Max(open, close) + upper, Low: math.Min(open, close) - lower})
	s.last = close
	s.t += 300
}

// Filler writes n alternating 2.0-range candles around price: true range 2.0, so the Wilder ATR settles at exactly 2.0 (an ordinary XAU M5 hour).
func (s *Scribe) Filler(n int, price float64) {
	for i := 0; i < n; i++ {
		if i%2 == 0 {
			s.Bar(price+0.3, 0.7, 0.7)
		} else {
			s.Bar(price-0.3, 0.7, 0.7)
		}
	}
}

// Reflect mirrors a series about center (price' = 2*center - price), turning a bullish fixture into its bearish twin with realistic positive prices.
func Reflect(bars []market.Candle, center float64) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, c := range bars {
		out[i] = market.Candle{Time: c.Time, Open: 2*center - c.Open, High: 2*center - c.Low, Low: 2*center - c.High, Close: 2*center - c.Close, Volume: c.Volume}
	}
	return out
}
