package ifvg

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// fixtureStart is an M5-aligned base time (Unix seconds).
const fixtureStart = int64(1_760_000_400 - 1_760_000_400%300)

// testConfig is the iFVG v3 contract at XAU scale (pip 0.1) with the production
// defaults of config/analysis.yml, the instrument's 5.0 entry cap and a 120 pip
// execution cap.
func testConfig() Config {
	return Config{
		ATRLength: 14, ATRWindowBars: 140, PivotBars: 2, EpisodeLookback: 48,
		MinimumGapATR: 0.5, MinimumGapPips: 2, MinimumOriginBodyATR: 0.5, MinimumOriginBody: 0.5, MaximumInversionBars: 72,
		MinimumBreakPips: 1, MinimumBreakATR: 0.05, MinInversionBody: 0.5, MinInversionCloseStr: 0.6, MinInversionDisp: 0.5,
		DisplacementGradeATR: 1.0, RoleFlipFailPips: 2, RoleFlipFailATR: 0.30,
		RetestMaxBars: 24, RetestTouchPips: 1, RetestTouchATR: 0.15, ConfirmationWindow: 3, ConfirmationMaxAge: 2,
		RejectionCloseStr: 0.6, RejectionWickRatio: 0.25, RejectionBodyRatio: 0.6, MaxRetestsPerInverse: 2, RetestDepartureATR: 1.0,
		EntryTolerancePips: 1, EntryToleranceATR: 0.15, MaximumEntryDistATR: 1.5, InvalidationBufferATR: 0.25,
		TargetLookbackBars: 96, TargetSwingBars: 12, TargetSwingATR: 1.0, MinimumTargetRoomATR: 0.55, MinimumRewardRisk: 1.15,
		ExpiryHours: 4, PipSize: 0.1, EntryMaxWidthPrice: 5, ExecutionStopMaxPips: 120,
	}
}

// scribe writes M5 candles one at a time so fixtures read as price paths.
type scribe struct {
	t    int64
	last float64
	bars []market.Candle
}

func newScribe(firstOpen int64, price float64) *scribe { return &scribe{t: firstOpen, last: price} }

// raw appends an explicit candle (open, high, low, close).
func (s *scribe) raw(open, high, low, close float64) *scribe {
	s.bars = append(s.bars, market.Candle{Time: s.t, Open: open, High: high, Low: low, Close: close})
	s.last = close
	s.t += 300
	return s
}

// bar appends a candle opening at the previous close with explicit wicks.
func (s *scribe) bar(close, upper, lower float64) *scribe {
	open := s.last
	return s.raw(open, math.Max(open, close)+upper, math.Min(open, close)-lower, close)
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

// reflectPrices mirrors a series about center, turning a bullish fixture into its
// bearish twin with realistic positive prices.
func reflectPrices(bars []market.Candle, center float64) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, c := range bars {
		out[i] = market.Candle{Time: c.Time, Open: 2*center - c.Open, High: 2*center - c.Low, Low: 2*center - c.High, Close: 2*center - c.Close, Volume: c.Volume}
	}
	return out
}

// marks are the landmarks of the bullish iFVG fixture (candle indices).
type marks struct {
	spike, a, b, c, invert, touch, confirm int
}

// opts parameterise bullishIFVG so a negative fixture changes exactly one fact of
// the positive one. Zero values are the positive fixture.
type opts struct {
	noSpike         bool    // no opposing swing high
	spikeUpper      float64 // wick of the opposing swing high above its 4111 close (default 20)
	gapTop          float64 // low of candle A (default 4116.0); the gap spans 4112..gapTop
	middleBody      float64 // middle candle close offset: its open is 4116.4, close = 4116.4 - middleBody (default 5.6)
	baseBars        int     // quiet candles between the gap and the inversion (default 3)
	invertClose     float64 // inversion candle close (default gapTop + 2.0)
	invertUpper     float64 // upper wick of the inversion candle (default 0.3)
	invertLow       float64 // inversion candle low (default 4110.8)
	earlyWeakClose  bool    // a candle closing 0.05 above the gap's top comes before the inversion candle
	waitBars        int     // quiet candles between the inversion and the retest
	touchLow        float64 // low of the retest touch candle (default gapTop + 0.2; <0 = no retest)
	rejectClose     float64 // rejection candle close (default gapTop + 2.2; <0 = none)
	rejectLow       float64 // rejection candle low (default gapTop - 0.6)
	rejectOpen      float64 // rejection candle open (default gapTop + 0.8)
	skipTouchCandle bool    // the rejection candle is itself the touch
}

func defaultOpts() opts {
	return opts{spikeUpper: 20, gapTop: 4116.0, middleBody: 5.6, baseBars: 3, invertUpper: 0.3, invertLow: 4110.8}
}

// bullishIFVG is a BUY: an opposing swing high ~4132 early, a bearish fair-value
// gap 4112.0-4116.0 made by a 5.6 point displacement candle, a base below it, a
// strong candle closing at 4118.0 (above the gap's top by far more than the
// buffer), a pullback touching the gap's top at 4116.2 and a bullish rejection
// candle closing at 4118.2. The last candle is the rejection (the confirmation).
func bullishIFVG(o opts) ([]market.Candle, marks) {
	s, m := ifvgScribe(o)
	return s.bars, m
}

func ifvgScribe(o opts) (*scribe, marks) {
	var m marks
	s := newScribe(fixtureStart, 4110)
	s.filler(40, 4110)
	// Opposing swing high: a candle closing at 4111 with a 20.0 wick -> ~4131.5,
	// with no gap on either side of it.
	s.bar(4110.6, 0.5, 0.5)
	if o.noSpike {
		s.bar(4111, 0.3, 0.3)
	} else {
		s.bar(4111, o.spikeUpper, 0.5)
	}
	m.spike = s.index()
	s.bar(4110.6, 0.5, 0.5).bar(4110.2, 0.5, 0.5)
	s.filler(25, 4110)
	// The bearish gap: A's low is above C's high.
	s.raw(4118.0, 4118.4, o.gapTop, o.gapTop+0.4)
	m.a = s.index()
	s.raw(4116.4, 4116.6, math.Min(4110.2, 4116.4-o.middleBody), 4116.4-o.middleBody)
	m.b = s.index()
	s.raw(4110.8, 4112.0, 4108.0, 4109.0)
	m.c = s.index()
	// Base below the gap: closes stay under its top.
	for i := 0; i < o.baseBars; i++ {
		s.bar(4110.5+0.3*float64(i%2), 0.6, 0.6)
	}
	if o.earlyWeakClose {
		s.raw(4110.6, o.gapTop+0.15, 4110.4, o.gapTop+0.05)
	}
	// The inversion candle: a strong bullish close above the gap's top.
	invertClose := o.invertClose
	if invertClose == 0 {
		invertClose = o.gapTop + 2.0
	}
	s.raw(4111.0, invertClose+o.invertUpper, o.invertLow, invertClose)
	m.invert = s.index()
	for i := 0; i < o.waitBars; i++ {
		s.bar(4119.0+0.4*float64(i%2), 0.4, 0.4)
	}
	if o.touchLow < 0 {
		return s, m
	}
	touchLow := o.touchLow
	if touchLow == 0 {
		touchLow = o.gapTop + 0.2
	}
	if !o.skipTouchCandle {
		// A pullback candle touching the gap's top.
		s.raw(o.gapTop+2.4, o.gapTop+2.6, touchLow, o.gapTop+1.6)
		m.touch = s.index()
	}
	if o.rejectClose < 0 {
		return s, m
	}
	rejectClose, rejectLow, rejectOpen := o.rejectClose, o.rejectLow, o.rejectOpen
	if rejectClose == 0 {
		rejectClose = o.gapTop + 2.2
	}
	if rejectLow == 0 {
		rejectLow = o.gapTop - 0.6
	}
	if rejectOpen == 0 {
		rejectOpen = o.gapTop + 0.8
	}
	s.raw(rejectOpen, math.Max(rejectClose, rejectOpen)+0.2, rejectLow, rejectClose)
	m.confirm = s.index()
	if o.skipTouchCandle {
		m.touch = m.confirm
	}
	return s, m
}

func nan() float64 { return math.NaN() }
