package crt

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// fixtureHour is an hour-aligned base time (Unix seconds). The anchor H1
// candle opens at fixtureHour and closes at fixtureHour+3600; M5 candle k
// (k=0 is the first one after the anchor closes) opens at
// fixtureHour+3600+300k.
const fixtureHour = int64(1_760_000_400 - 1_760_000_400%3600)

// testConfig is the CRT v3 contract at XAU scale (pip 0.1) with the
// production defaults of config/analysis.yml, except the execution stop cap,
// which tests set explicitly where they exercise it.
func testConfig() Config {
	return Config{
		MinimumH1RangeATR: 1.5, ATRLength: 14, ATRWindowBars: 140, SweepWindowH1Periods: 1,
		MinimumSweepPips: 2, MinimumSweepATR: 0.10, MinimumReclaimPips: 1, MinimumReclaimATR: 0.05, ReclaimMaxBars: 6,
		ConfirmationMode: ConfirmationMSS, StructurePivotBars: 2, StructureLookbackBars: 24, MSSMaxBars: 12,
		MinimumMSSBodyRatio: 0.5, MinimumMSSDisplacement: 0.5, MinimumMSSCloseStrength: 0.6, DisplacementGradeATR: 1.0,
		ConfirmationMaxAgeBars: 2, ConfirmationBufferPips: 0,
		EntryModel: EntryMSSRetest, EntryDepthATR: 0.5, EntryToleranceATR: 0.1, EntryMaxWidthPrice: 5.0,
		InvalidationBufferATR: 0.25, MinimumTargetRoomATR: 0.55, MinimumRewardRisk: 1.15, ExecutionStopMaxPips: 120,
		ExpiryHours: 4, PipSize: 0.1,
	}
}

// scribe writes M5 candles from close-to-close waypoints: each candle opens at
// the previous close and carries explicit wicks, so fixtures read as price
// paths with real structure instead of raw OHLC tuples.
type scribe struct {
	t    int64
	last float64
	bars []market.Candle
}

func newScribe(firstOpen int64, price float64) *scribe {
	return &scribe{t: firstOpen, last: price}
}

func (s *scribe) bar(close, upper, lower float64) {
	open := s.last
	s.bars = append(s.bars, market.Candle{
		Time: s.t, Open: open, Close: close,
		High: math.Max(open, close) + upper, Low: math.Min(open, close) - lower,
	})
	s.last = close
	s.t += 300
}

// filler writes n alternating 2.0-range candles around price: true range 2.0,
// so the Wilder ATR settles at exactly 2.0 (an ordinary XAU M5 hour).
func (s *scribe) filler(n int, price float64) {
	for i := 0; i < n; i++ {
		if i%2 == 0 {
			s.bar(price+0.3, 0.7, 0.7)
		} else {
			s.bar(price-0.3, 0.7, 0.7)
		}
	}
}

// h1Series returns count closed H1 candles ending with the anchor, which opens
// at anchorOpen. Filler candles have a 10.0 range at 4110 (ATR 10).
func h1Series(anchorOpen int64, count int, anchorHigh, anchorLow float64) []market.Candle {
	out := make([]market.Candle, count)
	for i := range out {
		out[i] = market.Candle{Time: anchorOpen - int64(count-1-i)*3600, Open: 4110, High: 4115, Low: 4105, Close: 4110}
	}
	out[count-1] = market.Candle{Time: anchorOpen, Open: 4110, High: anchorHigh, Low: anchorLow, Close: 4110}
	return out
}

// reflectPrices mirrors a series about center (price' = 2*center - price), turning
// a bullish fixture into its bearish twin with realistic positive prices.
func reflectPrices(bars []market.Candle, center float64) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, c := range bars {
		out[i] = market.Candle{
			Time: c.Time, Open: 2*center - c.Open, High: 2*center - c.Low, Low: 2*center - c.High, Close: 2*center - c.Close, Volume: c.Volume,
		}
	}
	return out
}

// bullishCRT is the canonical accepted XAU BUY: anchor H1 [4100, 4118]
// (range 18, ATR 10.57), a lower-high structure into the H1 low, a sweep
// candle that pierces 4100 by 1.4 and closes back inside, and a displacement
// candle that closes above the 4105.6 swing high. It returns the M5 series
// through the structure-shift candle (the last candle) plus the named candle
// indices.
func bullishCRT() (m5 []market.Candle, h1 []market.Candle, idx scenarioIndex) {
	return bullishCRTWith(60, 40)
}

// bullishCRTWith is bullishCRT with explicit warm-up depths: nFill filler M5
// candles before the anchor closes and h1Count closed H1 candles (anchor
// included).
func bullishCRTWith(nFill, h1Count int) (m5 []market.Candle, h1 []market.Candle, idx scenarioIndex) {
	s := bullishPrefix(nFill)
	s.bar(4101.0, 0.3, 1.9) // k9 sweep: low 4098.6, reclaims on the same candle
	s.bar(4103.0, 0.3, 0.3) // k10
	s.bar(4106.4, 0.3, 0.2) // k11 closes above 4105.6: structure shift
	idx = scenarioIndex{first: nFill, sweep: nFill + 9, reclaim: nFill + 9, shift: nFill + 11}
	return s.bars, h1Series(fixtureHour, h1Count, 4118, 4100), idx
}

// bullishPrefix writes the M5 path up to (and including) k8: nFill filler
// candles, then a lower-high decline into the H1 low with a swing high of
// 4105.6 at k5 and a test of 4100 at k8 that is NOT yet a sweep.
func bullishPrefix(nFill int) *scribe {
	s := newScribe(fixtureHour+3600-int64(nFill)*300, 4110)
	s.filler(nFill, 4110)
	s.bar(4108.0, 0.5, 0.5) // k0
	s.bar(4106.5, 0.4, 0.5) // k1
	s.bar(4104.8, 0.4, 0.6) // k2
	s.bar(4103.5, 0.3, 0.6) // k3
	s.bar(4104.2, 0.4, 0.4) // k4 bounce
	s.bar(4105.2, 0.4, 0.3) // k5 swing high 4105.6 (pivot, n=2)
	s.bar(4103.0, 0.2, 0.6) // k6
	s.bar(4101.2, 0.3, 0.6) // k7
	s.bar(4100.5, 0.3, 0.5) // k8 tests the low without a real sweep
	return s
}

type scenarioIndex struct {
	first, sweep, reclaim, shift int
}

func (i scenarioIndex) k(n int) int { return i.first + n }
