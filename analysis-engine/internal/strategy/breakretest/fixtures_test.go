package breakretest

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// fixtureStart is an M5-aligned base time (Unix seconds).
const fixtureStart = int64(1_760_000_400 - 1_760_000_400%300)

// testConfig is the Break & Retest v3 contract at XAU scale (pip 0.1) with the
// production defaults of config/analysis.yml and a 120 pip execution cap.
func testConfig() Config {
	return Config{
		ATRLength: 14, ATRWindowBars: 140,
		PivotBars: 2, ReferenceLookback: 120, EpisodeLookback: 48, LevelMinTouches: 2,
		LevelClusterPips: 2, LevelClusterATR: 0.4, LevelSpanMultiple: 2,
		LineMinSpanBars: 6, LineMinSlopeATR: 0.01, LineMaxSlopeATR: 0.10, LineWickATR: 0.25, LineMaxGapRatio: 1.5, PreBreakBars: 3,
		BreakoutAcceptBars: 2, BreakBufferPips: 1, BreakBufferATR: 0.05,
		MinBreakBodyRatio: 0.5, MinBreakCloseStrength: 0.6, MinBreakDisplacement: 0.5, DisplacementGradeATR: 1.0,
		BreakFailPips: 2, BreakFailATR: 0.30,
		RetestMaxBars: 24, RetestTouchPips: 1, RetestTouchATR: 0.15, ConfirmationWindowBars: 3, ConfirmationMaxAgeBars: 2,
		RejectionCloseStrength: 0.6, RejectionWickRatio: 0.25, RejectionBodyRatio: 0.6,
		ProtectedStructure: ProtectedLastPivot, EntryTolerancePips: 1, EntryToleranceATR: 0.15, MaximumEntryDistanceATR: 1.5,
		InvalidationBufferATR: 0.25, TargetLookbackBars: 96, TargetSwingBars: 12, TargetSwingATR: 1.0,
		MinimumTargetRoomATR: 0.55, MinimumRewardRisk: 1.15, ExecutionStopMaxPips: 120, ExpiryHours: 4, PipSize: 0.1,
		PublishedOverallQuality: 0.75,
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

func newScribe(firstOpen int64, price float64) *scribe { return &scribe{t: firstOpen, last: price} }

func (s *scribe) bar(close, upper, lower float64) *scribe {
	open := s.last
	s.bars = append(s.bars, market.Candle{
		Time: s.t, Open: open, Close: close,
		High: math.Max(open, close) + upper, Low: math.Min(open, close) - lower,
	})
	s.last = close
	s.t += 300
	return s
}

// filler writes n alternating 2.0-range candles around price: true range 2.0, so
// the Wilder ATR settles at exactly 2.0 (an ordinary XAU M5 stretch).
func (s *scribe) filler(n int, price float64) *scribe {
	for i := 0; i < n; i++ {
		if i%2 == 0 {
			s.bar(price+0.3, 0.7, 0.7)
		} else {
			s.bar(price-0.3, 0.7, 0.7)
		}
	}
	return s
}

func (s *scribe) index() int { return len(s.bars) - 1 }

// reflectPrices mirrors a series about center (price' = 2*center - price),
// turning a bullish fixture into its bearish twin with realistic positive
// prices.
func reflectPrices(bars []market.Candle, center float64) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, c := range bars {
		out[i] = market.Candle{Time: c.Time, Open: 2*center - c.Open, High: 2*center - c.Low, Low: 2*center - c.High, Close: 2*center - c.Close, Volume: c.Volume}
	}
	return out
}

// marks are the indices of the landmarks of a bullish key-level fixture.
type marks struct {
	spike, pivot1, pivot2, pivotLow, brk, accept, touch, confirm int
}

// levelOptions parameterise bullishLevel so the negative fixtures change
// exactly one fact of the positive one.
type levelOptions struct {
	secondTouch   bool    // the second resistance touch exists
	breakClose    float64 // close of the first beyond-candle
	breakUpper    float64
	acceptClose   float64 // close of the accepting candle (0 = no accepting candle: price falls back)
	fallBackClose float64
	waitBars      int     // quiet candles between acceptance and the retest
	touchLow      float64 // retest low (0 = no retest)
	rejectClose   float64 // rejection candle close (0 = none)
	spikeUpper    float64 // wick of the opposing swing high above 4124 close
	noSpike       bool    // no opposing swing high at all
}

func defaultLevel() levelOptions {
	return levelOptions{secondTouch: true, breakClose: 4122.4, breakUpper: 0.3, acceptClose: 4123.4, waitBars: 0, touchLow: 4120.1, rejectClose: 4122.6, spikeUpper: 8}
}

// bullishLevel is a BUY: an opposing swing high at ~4132 early, then two touches
// of resistance ~4120, a base below it, a strong two-close break, a pullback
// that touches the level and a bullish rejection candle. The last candle is the
// rejection (the confirmation).
func bullishLevel(o levelOptions) ([]market.Candle, marks) {
	s, m := levelScribe(o)
	return s.bars, m
}

// levelScribe is bullishLevel before the series is extracted, so a test can keep
// writing candles after the rejection.
func levelScribe(o levelOptions) (*scribe, marks) {
	var m marks
	s := newScribe(fixtureStart, 4110)
	s.filler(60, 4110)
	// Opposing swing high: 4124 close with an 8.0 wick -> 4132.
	s.bar(4114, 0.5, 0.5).bar(4118, 0.5, 0.5)
	if o.noSpike {
		s.bar(4116, 0.3, 0.3)
	} else {
		s.bar(4124, o.spikeUpper, 0.5)
	}
	m.spike = s.index()
	s.bar(4118, 0.5, 0.5).bar(4112, 0.5, 0.5).bar(4110, 0.5, 0.5)
	s.filler(8, 4110)
	// First touch of resistance: high 4119.9.
	s.bar(4113, 0.5, 0.5).bar(4116, 0.5, 0.5)
	s.bar(4119, 0.9, 0.5)
	m.pivot1 = s.index()
	s.bar(4116, 0.5, 0.5).bar(4113.5, 0.5, 0.5) // pull back
	// Protected structure: a swing low ~4112.4.
	s.bar(4112.9, 0.3, 0.5)
	m.pivotLow = s.index()
	s.bar(4115, 0.5, 0.5).bar(4117, 0.5, 0.5)
	if o.secondTouch {
		s.bar(4118.5, 0.4, 0.5)
		s.bar(4119.5, 0.5, 0.5) // high 4120.0
		m.pivot2 = s.index()
		s.bar(4117, 0.5, 0.5).bar(4115.5, 0.5, 0.5)
	} else {
		s.bar(4118, 0.4, 0.5).bar(4116, 0.5, 0.5).bar(4115.5, 0.5, 0.5)
	}
	s.bar(4117, 0.5, 0.5).bar(4118, 0.5, 0.5).bar(4118.5, 0.5, 0.5) // base below the level
	// Break.
	s.bar(o.breakClose, o.breakUpper, 0.1)
	m.brk = s.index()
	if o.acceptClose == 0 {
		s.bar(o.fallBackClose, 0.3, 0.3)
		return s, m
	}
	s.bar(o.acceptClose, 0.3, 0.2)
	m.accept = s.index()
	for i := 0; i < o.waitBars; i++ {
		s.bar(4124, 0.4, 0.4)
	}
	if o.touchLow == 0 {
		return s, m
	}
	// Retest: a pullback bar touching the level, then the rejection.
	s.bar(4121.0, 0.2, 4121.0-o.touchLow)
	m.touch = s.index()
	if o.rejectClose == 0 {
		return s, m
	}
	s.bar(o.rejectClose, 0.3, 0.8)
	m.confirm = s.index()
	return s, m
}

// lineMarks are the landmarks of a bullish trendline fixture.
type lineMarks struct {
	spike, anchorA, anchorB, brk, accept, touch, confirm int
	slope, atB                                           float64
}

// lineAt is the fixture line's value at candle index i.
func (m lineMarks) lineAt(i int) float64 { return m.atB + m.slope*float64(i-m.anchorB) }

// bullishLine is a BUY on a descending resistance line through two pivot highs
// (4125.0 and 4119.0, thirty-two candles apart: about -0.19 per candle), broken
// upward with two accepted closes, retested at the line's value AT THE RETEST
// CANDLE (not at the last candle), and confirmed by a bullish rejection.
// extraBars appends quiet candles after the confirmation so the line's value at
// the retest differs from its value at the last candle.
func bullishLine(extraBars int) ([]market.Candle, lineMarks) {
	var m lineMarks
	s := newScribe(fixtureStart, 4110)
	s.filler(60, 4110)
	s.bar(4114, 0.5, 0.5).bar(4118, 0.5, 0.5)
	s.bar(4124, 8, 0.5) // opposing swing high 4132, above the line's own anchor
	m.spike = s.index()
	s.bar(4118, 0.5, 0.5).bar(4112, 0.5, 0.5).bar(4110, 0.5, 0.5)
	s.filler(8, 4110)
	s.bar(4114, 0.5, 0.5).bar(4118, 0.5, 0.5).bar(4124, 1.0, 0.5) // anchor A: high 4125.0
	m.anchorA = s.index()
	// A V between the anchors: highs fall, then rise, so no pivot high sits
	// between them and the two anchors are chronologically adjacent.
	const down, up = 12, 18
	for k := 1; k <= down; k++ {
		s.bar(4121+(4109.5-4121)*float64(k)/down, 0.5, 0.5)
	}
	for k := 1; k <= up; k++ {
		s.bar(4109.5+(4118.3-4109.5)*float64(k)/up, 0.5, 0.5)
	}
	s.bar(4118.5, 0.5, 0.5) // anchor B: high 4119.0
	m.anchorB = s.index()
	m.atB = 4119.0
	m.slope = (4119.0 - 4125.0) / float64(m.anchorB-m.anchorA)
	s.bar(4116, 0.5, 0.5).bar(4114, 0.5, 0.5).bar(4114.5, 0.4, 0.5).bar(4115, 0.4, 0.5).bar(4115.5, 0.4, 0.5)
	s.bar(4120.0, 0.2, 0.1) // break: well above the line
	m.brk = s.index()
	s.bar(4121.0, 0.3, 0.2)
	m.accept = s.index()
	next := s.index() + 1
	s.bar(4118.6, 0.2, 4118.6-(m.lineAt(next)+0.1)) // pullback touching the line
	m.touch = s.index()
	next = s.index() + 1
	s.bar(m.lineAt(next)+1.8, 0.3, 0.9) // bullish rejection closing well above the line
	m.confirm = s.index()
	for i := 0; i < extraBars; i++ {
		s.bar(s.last+0.2, 0.3, 0.3)
	}
	return s.bars, m
}

func nan() float64 { return math.NaN() }
