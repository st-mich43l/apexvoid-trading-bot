package sessionlevel

import (
	"math"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// dayAnchor is a Wednesday 00:00 UTC. The fixtures run from the Asia session that
// opened the evening before (Tue 22:00), through London (07:00-13:00) and into the
// New York session, so every window they read is a real UTC window.
var dayAnchor = time.Date(2025, 10, 15, 0, 0, 0, 0, time.UTC).Unix()

const (
	asiaBars   = 108 // 22:00 -> 07:00
	londonBars = 72  // 07:00 -> 13:00
)

// testConfig is the Session Level v4 contract at XAU scale (pip 0.1) with the
// production defaults of config/analysis.yml and a 120 pip execution cap.
func testConfig() Config {
	return Config{
		ATRLength: 14, ATRWindowBars: 140, PivotBars: 2, EpisodeLookback: 48,
		AsiaStartHour: 22, LondonStartHour: 7, NYStartHour: 13, RolloverHour: 21,
		SessionStartToleranceMin: 90, SessionEndToleranceMin: 90, SessionMaxGapMin: 60, SessionMinCoverage: 0.8,
		PeriodStartToleranceMin: 120, PeriodEndToleranceMin: 120, PeriodMaxGapMin: 150, PeriodMinCoverage: 0.85,
		SessionMaxAgeHours: 30, DayMaxAgeHours: 72, WeekMaxAgeHours: 168,
		TouchTolerancePips: 2, TouchToleranceATR: 0.10, ApproachLookback: 24, ApproachMinATR: 0.5, TouchSeparationBars: 3,
		MaximumPriorTouches: 1, HoldBufferPips: 1, HoldBufferATR: 0.05, FailBufferPips: 3, FailBufferATR: 0.25,
		SweepThresholdPips: 3, SweepThresholdATR: 0.10, ReclaimBufferPips: 1, ReclaimBufferATR: 0.05, ReclaimMaxBars: 6,
		BreakoutAccept: 2, BreakoutBufferPips: 2, BreakoutBufferATR: 0.15,
		RejectionCloseStrength: 0.6, RejectionWickRatio: 0.25, RejectionBodyRatio: 0.6, DisplacementGradeATR: 1.0,
		ConfirmationWindow: 3, ConfirmationMaxAge: 2, MaxEpisodesPerLevel: 3,
		EntryBandATR: 0.5, EntryTolerancePips: 1, EntryToleranceATR: 0.15, MaximumEntryDistATR: 1.5, InvalidationBufferATR: 0.25,
		TargetLookbackBars: 96, TargetSwingBars: 12, TargetSwingATR: 1.0, MinimumTargetRoomATR: 0.55, MinimumRewardRisk: 1.15,
		ExpiryHours: 4, PipSize: 0.1, ExecutionStopMaxPips: 120,
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

// skipTo moves the clock forward, leaving a hole in the feed.
func (s *scribe) skipTo(t int64) *scribe { s.t = t; return s }

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

// marks are the landmarks of the bullish Session Level fixture (candle indices).
type marks struct {
	low, top, touch, sweep, confirm int
}

// opts parameterise the fixtures so a negative fixture changes exactly one fact of
// the positive one. Zero values are the positive fixture.
type opts struct {
	low        float64 // the London low, the level under test (default 4104.0)
	noRally    bool    // no opposing swing high above the entry
	dwell      bool    // no fresh approach: price sits just above the level for 30 candles
	asia       bool    // the level is the ASIA low (formed overnight), reacted to in London
	prior      int     // earlier touches of the level (V-shaped dips that do not close through it)
	priorSweep bool    // the level was swept (and recovered) before the reaction
	// first-touch tail
	touchLow    float64 // low of the touch candle (default low+0.1)
	rejectClose float64 // rejection candle close (default low+2.0)
	rejectOpen  float64 // rejection candle open (default low+0.5)
	rejectLow   float64 // rejection candle low (default low+0.15)
	weakReject  bool    // the candle after the touch closes bullish but weakly
	waitBars    int     // quiet candles between the touch and the rejection
	// sweep tail
	sweepLow     float64 // low of the sweep candle (default low-0.6)
	sweepClose   float64 // close of the sweep candle (default low-0.2)
	reclaimClose float64 // reclaim candle close (default low+2.0)
	reclaimGap   int     // quiet candles below the level between the sweep and the reclaim
	gapClose     float64 // close of those candles (default low-0.15)
	noReclaim    bool    // the sweep is never reclaimed
	afterBars    int     // quiet candles after the confirmation (an aged reaction)
}

func (o opts) lowOr() float64 {
	if o.low == 0 {
		return 4104.0
	}
	return o.low
}

// dayPrefix writes the Asia session, the London session with its low at o.low, a
// quiet start to New York, a rally to a swing high and a pullback that ends 2.2
// above the level: everything up to (not including) the reaction.
func dayPrefix(o opts) (*scribe, marks) {
	var m marks
	low := o.lowOr()
	s := newScribe(dayAnchor-2*3600, 4120) // Tue 22:00
	if o.asia {
		// The Asia low is the level: a dip at ~01:00, then a quiet Asia, then London
		// starts above it. London's own range stays above the level.
		s.filler(30, 4112)
		s.bar(4110, 0.2, 0.3).bar(4108, 0.2, 0.3)
		s.bar(4108.4, 0.3, 4108.0-low)
		m.low = s.index()
		s.filler(asiaBars-33, 4108.5)
		s.filler(20, 4108.5) // London 07:00 -> 08:40
	} else {
		s.filler(asiaBars, 4120)
		s.filler(6, 4120)
		for _, c := range []float64{4118, 4116, 4114, 4112, 4110, 4108} {
			s.bar(c, 0.2, 0.3)
		}
		s.bar(4108.4, 0.3, 4108.0-low)
		m.low = s.index()
		s.filler(londonBars-6-6-1, 4108.5)
		s.filler(20, 4108.5) // 13:00 -> 14:40, New York
	}
	if o.priorSweep {
		s.raw(4107.0, 4107.1, low-0.6, 4106.0)
		s.bar(4108.0, 0.3, 0.3).bar(4108.5, 0.3, 0.3)
	}
	for i := 0; i < o.prior; i++ {
		s.bar(4106.5, 0.2, 0.2)
		s.raw(4106.4, 4106.6, low+0.1, low+0.5)
		s.bar(4107.0, 0.2, 0.3).bar(4108.0, 0.2, 0.3).bar(4108.5, 0.2, 0.3)
	}
	if o.dwell {
		for i := 0; i < 30; i++ {
			s.raw(4104.8, 4106.3, low+0.4, low+0.7)
		}
		return s, m
	}
	if !o.noRally {
		for _, c := range []float64{4110.5, 4112.5, 4114.5, 4116.5, 4118.5} {
			s.bar(c, 0.3, 0.3)
		}
		m.top = s.index()
		for _, c := range []float64{4116, 4113, 4110, 4107} {
			s.bar(c, 0.3, 0.3)
		}
	} else {
		s.bar(4107.5, 0.3, 0.3).bar(4107.0, 0.3, 0.3)
	}
	s.bar(low+2.2, 0.2, 0.3)
	return s, m
}

// firstTouchDay is a BUY first-touch rejection of the London low: a touch candle
// that trades to the level without closing through it, then a bullish rejection
// candle closing back above it. The last candle is the confirmation.
func firstTouchDay(o opts) ([]market.Candle, marks) {
	s, m := dayPrefix(o)
	low := o.lowOr()
	touchLow := o.touchLow
	if touchLow == 0 {
		touchLow = low + 0.1
	}
	s.raw(low+1.0, low+1.3, touchLow, low+0.6)
	m.touch = s.index()
	for i := 0; i < o.waitBars; i++ {
		s.raw(low+0.8, low+1.0, low+0.5, low+0.7)
	}
	if o.weakReject {
		s.raw(low+0.5, low+1.6, low+0.3, low+0.8)
	}
	rc, ro, rl := o.rejectClose, o.rejectOpen, o.rejectLow
	if rc == 0 {
		rc = low + 2.0
	}
	if ro == 0 {
		ro = low + 0.5
	}
	if rl == 0 {
		rl = low + 0.15
	}
	s.raw(ro, math.Max(rc, ro)+0.1, rl, rc)
	m.confirm = s.index()
	for i := 0; i < o.afterBars; i++ {
		s.raw(low+2.0, low+2.5, low+1.6, low+2.1)
	}
	return s.bars, m
}

// sweepDay is a BUY sweep and reclaim of the London low: a candle trading beyond it
// by more than the threshold, then a strong candle closing back above it.
func sweepDay(o opts) ([]market.Candle, marks) {
	s, m := dayPrefix(o)
	low := o.lowOr()
	sl, sc := o.sweepLow, o.sweepClose
	if sl == 0 {
		sl = low - 0.6
	}
	if sc == 0 {
		sc = low - 0.2
	}
	s.raw(low+1.0, low+1.1, sl, sc)
	m.sweep, m.touch = s.index(), s.index()
	for i := 0; i < o.reclaimGap; i++ {
		gc := o.gapClose
		if gc == 0 {
			gc = low - 0.15
		}
		s.raw(low-0.1, low+0.1, math.Min(gc, low-0.3), gc)
	}
	if o.noReclaim {
		for i := 0; i < 8; i++ {
			s.raw(low-0.1, low+0.1, low-0.3, low-0.15)
		}
		m.confirm = -1
		return s.bars, m
	}
	rc := o.reclaimClose
	if rc == 0 {
		rc = low + 2.0
	}
	s.raw(low-0.1, rc+0.1, low-0.2, rc)
	m.confirm = s.index()
	for i := 0; i < o.afterBars; i++ {
		s.raw(low+2.0, low+2.5, low+1.6, low+2.1)
	}
	return s.bars, m
}

// hourly writes H1 candles from start (Unix) for n hours, skipping the daily
// 21:00-22:00 UTC hour the way the XAU feed does, around price with a 4.0 range.
func hourly(start int64, hours int, price float64, skip func(t int64) bool) []market.Candle {
	var out []market.Candle
	for i := 0; i < hours; i++ {
		t := start + int64(i)*3600
		if skip != nil && skip(t) {
			continue
		}
		out = append(out, market.Candle{Time: t, Open: price, High: price + 2, Low: price - 2, Close: price})
	}
	return out
}

func breakHour(t int64) bool { return time.Unix(t, 0).UTC().Hour() == 21 }

// withSpike replaces the H1 candle opening at t with one carrying a single extreme.
func withSpike(bars []market.Candle, t int64, high, low float64) []market.Candle {
	out := append([]market.Candle(nil), bars...)
	for i, c := range out {
		if c.Time == t {
			if high != 0 {
				out[i].High = high
			}
			if low != 0 {
				out[i].Low = low
			}
		}
	}
	return out
}

func nan() float64 { return math.NaN() }
