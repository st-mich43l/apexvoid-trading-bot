package crt

import (
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// bullishWith finishes bullishPrefix with a caller-written tail and returns the
// M5 series and the matching H1 series.
func bullishWith(tail func(s *scribe)) ([]market.Candle, []market.Candle) {
	s := bullishPrefix(60)
	tail(s)
	return s.bars, h1Series(fixtureHour, 40, 4118, 4100)
}

func evalAt(t *testing.T, cfg Config, h1, m5 []market.Candle, asOf int) Analysis {
	t.Helper()
	return detectAsOf(cfg, Input{H1: h1, M5: m5}, asOf)
}

func TestSweepBeforeTheAnchorClosesNeverQualifies(t *testing.T) {
	m5, _, _ := bullishCRT()
	// The H1 candle that defines this range closes at k10, AFTER the sweep
	// candle (k9): the sweep belongs to the previous hour, not to this range.
	k10 := m5[60+10].Time
	h1 := h1Series(k10-3600, 40, 4118, 4100)
	a := Detect(testConfig(), Input{H1: h1, M5: m5})
	if len(a.Setups) != 0 {
		t.Fatalf("a sweep that predates the anchor's close produced %+v", a.Setups)
	}
}

func TestStaleAnchorIsBoundedByTheSweepWindow(t *testing.T) {
	m5, _, _ := bullishCRT()
	// The anchor closed two hours before the sweep.
	stale := h1Series(fixtureHour-2*3600, 40, 4118, 4100)
	if a := Detect(testConfig(), Input{H1: stale, M5: m5}); len(a.Setups) != 0 {
		t.Fatalf("default one-period window accepted a two-hour-old anchor: %+v", a.Setups)
	}
	// A wider window is an explicit, bounded configuration choice.
	wide := testConfig()
	wide.SweepWindowH1Periods = 3
	if a := Detect(wide, Input{H1: stale, M5: m5}); len(a.Setups) != 1 {
		t.Fatalf("a configured 3-period window should reach the sweep: %+v", a)
	}
	// And it stays bounded: three hours after the close is out of the window.
	older := h1Series(fixtureHour-3*3600, 40, 4118, 4100)
	if a := Detect(wide, Input{H1: older, M5: m5}); len(a.Setups) != 0 {
		t.Fatalf("anchor older than the configured window produced %+v", a.Setups)
	}
}

func TestPenetrationBelowTheSweepThresholdIsNotASweep(t *testing.T) {
	// 4099.85 is below the H1 low (4100) but inside the 0.2156 threshold
	// (max(2 pips, 0.10 ATR)), so the market never took the liquidity.
	m5, h1 := bullishWith(func(s *scribe) {
		s.bar(4101.0, 0.3, 0.65) // low 4099.85
		s.bar(4103.0, 0.3, 0.3)
		s.bar(4106.4, 0.3, 0.2)
	})
	a := Detect(testConfig(), Input{H1: h1, M5: m5})
	if len(a.Setups) != 0 || len(a.Rejections) != 0 {
		t.Fatalf("a sub-threshold pierce produced setups %+v rejections %+v", a.Setups, a.Rejections)
	}
}

func TestNoReclaimWithinTheWindowIsRejected(t *testing.T) {
	m5, h1 := bullishWith(func(s *scribe) {
		s.bar(4099.5, 0.3, 0.9) // k9 sweeps, closes below the low
		for i := 0; i < 7; i++ {
			s.bar(4099.2-0.1*float64(i), 0.2, 0.3) // k10..k16 stay below 4100
		}
	})
	a := evalAt(t, testConfig(), h1, m5, len(m5)-1)
	if len(a.Setups) != 0 || !hasRejection(a, ReasonReclaimWindowExpired) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
	// While the window is still open the episode is pending, not rejected.
	pending := evalAt(t, testConfig(), h1, m5, 60+12)
	if len(pending.Setups) != 0 || len(pending.Rejections) != 0 {
		t.Fatalf("an open reclaim window must stay silent: %+v", pending)
	}
}

func TestCloseThroughTheWholeRangeIsNotAReclaim(t *testing.T) {
	m5, h1 := bullishWith(func(s *scribe) {
		s.bar(4119.0, 0.3, 21.0) // sweeps then closes ABOVE the H1 high
	})
	a := Detect(testConfig(), Input{H1: h1, M5: m5})
	if len(a.Setups) != 0 || !hasRejection(a, ReasonReclaimOvershoot) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
}

func TestReclaimWithoutAStructureShiftNeverConfirms(t *testing.T) {
	m5, h1 := bullishWith(func(s *scribe) {
		s.bar(4101.0, 0.3, 1.9)   // k9 sweep + reclaim
		for i := 0; i < 14; i++ { // chop that never closes above 4105.6
			s.bar(4102.5+0.4*float64(i%2), 0.4, 0.4)
		}
	})
	// Still inside the 12-bar structure window: pending, no rejection yet.
	if a := evalAt(t, testConfig(), h1, m5, 60+9+6); len(a.Setups) != 0 || len(a.Rejections) != 0 {
		t.Fatalf("pending episode must be silent: %+v", a)
	}
	a := evalAt(t, testConfig(), h1, m5, len(m5)-1)
	if len(a.Setups) != 0 || !hasRejection(a, ReasonMSSWindowExpired) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
}

func TestBreakAboveTheSwingWithoutDisplacementIsRejected(t *testing.T) {
	m5, h1 := bullishWith(func(s *scribe) {
		s.bar(4101.0, 0.3, 1.9) // k9 sweep + reclaim
		s.bar(4103.0, 0.3, 0.3)
		// Closes 0.1 above the swing but is a doji with huge wicks.
		s.bar(4105.7, 3.0, 2.7)
		for i := 0; i < 13; i++ {
			s.bar(4102.5+0.4*float64(i%2), 0.4, 0.4)
		}
	})
	a := Detect(testConfig(), Input{H1: h1, M5: m5})
	if len(a.Setups) != 0 || !hasRejection(a, ReasonMSSQualityInsufficient) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
}

func TestOppositeStructureLosesTheReclaim(t *testing.T) {
	m5, h1 := bullishWith(func(s *scribe) {
		s.bar(4101.0, 0.3, 1.9) // k9 sweep + reclaim
		s.bar(4099.2, 0.3, 0.4) // k10 closes back below the H1 low: bearish shift
	})
	a := Detect(testConfig(), Input{H1: h1, M5: m5})
	if len(a.Setups) != 0 || !hasRejection(a, ReasonReclaimLost) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
}

func TestBothExtremesRaidedIsAmbiguousUntilCoherent(t *testing.T) {
	// The high is raided AFTER the low sweep and before the confirmation: the
	// market took both sides and the order is not directionally coherent.
	during, h1 := bullishWith(func(s *scribe) {
		s.bar(4101.0, 0.3, 1.9)  // k9 sweep + reclaim
		s.bar(4103.0, 16.5, 0.3) // k10 spikes to 4119.5 (> 4118 + threshold), closes inside
		s.bar(4106.4, 0.3, 0.2)  // k11 would shift structure
	})
	a := Detect(testConfig(), Input{H1: h1, M5: during})
	if len(a.Setups) != 0 || !hasRejection(a, ReasonBothExtremesRaided) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}

	// The high is raided EARLIER and closed back inside before the low sweep:
	// the later raid is the coherent one and resolves the ambiguity.
	s := newScribe(fixtureHour+3600-60*300, 4110)
	s.filler(60, 4110)
	s.bar(4108.0, 11.5, 0.5) // k0: high 4121.2 raid, closes back inside
	s.bar(4106.5, 0.4, 0.5)
	s.bar(4104.8, 0.4, 0.6)
	s.bar(4103.5, 0.3, 0.6)
	s.bar(4104.2, 0.4, 0.4)
	s.bar(4105.2, 0.4, 0.3)
	s.bar(4103.0, 0.2, 0.6)
	s.bar(4101.2, 0.3, 0.6)
	s.bar(4100.5, 0.3, 0.5)
	s.bar(4101.0, 0.3, 1.9)
	s.bar(4103.0, 0.3, 0.3)
	s.bar(4106.4, 0.3, 0.2)
	resolved := Detect(testConfig(), Input{H1: h1, M5: s.bars})
	setup := mustOneSetup(t, resolved)
	if !setup.DoubleRaidResolved || setup.Direction != market.Buy {
		t.Fatalf("resolved double raid = %+v", setup)
	}
}

func TestTechnicalStopIsRejectedNotTightenedWhenRiskExceedsTheEnvelope(t *testing.T) {
	m5, h1, _ := bullishCRT()
	cfg := testConfig()
	cfg.ExecutionStopMaxPips = 50 // the setup's honest risk is ~78 pips
	a := Detect(cfg, Input{H1: h1, M5: m5})
	if len(a.Setups) != 0 || !hasRejection(a, ReasonRiskExceedsEnvelope) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
	// With room in the envelope the same setup keeps the full, honest stop.
	cfg.ExecutionStopMaxPips = 90
	s := mustOneSetup(t, Detect(cfg, Input{H1: h1, M5: m5}))
	if !(s.Stop < s.Sweep.ExtremePrice) || s.RiskPips > 90 {
		t.Fatalf("stop %v extreme %v risk %v", s.Stop, s.Sweep.ExtremePrice, s.RiskPips)
	}
}

func TestInsufficientRoomAndRewardRiskAreRejected(t *testing.T) {
	m5, h1, _ := bullishCRT()
	room := testConfig()
	room.MinimumTargetRoomATR = 20
	if a := Detect(room, Input{H1: h1, M5: m5}); len(a.Setups) != 0 || !hasRejection(a, ReasonInsufficientTargetRoom) {
		t.Fatalf("room: %+v", a)
	}
	rr := testConfig()
	rr.MinimumRewardRisk = 3
	if a := Detect(rr, Input{H1: h1, M5: m5}); len(a.Setups) != 0 || !hasRejection(a, ReasonRewardRiskBelowMinimum) {
		t.Fatalf("rr: %+v", a)
	}
}

func TestH1RangeBelowTheMinimumIsRejectedOnlyWhenSwept(t *testing.T) {
	m5, _, _ := bullishCRT()
	// Range 11 / ATR ~10 = 1.1 < 1.5, and the market swept its low.
	narrow := h1Series(fixtureHour, 40, 4111, 4100)
	a := Detect(testConfig(), Input{H1: narrow, M5: m5})
	if len(a.Setups) != 0 || !hasRejection(a, ReasonH1RangeBelowMinimum) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
}

func TestConfirmationAgeIsBounded(t *testing.T) {
	m5, h1, idx := bullishCRT()
	s := &scribe{t: m5[len(m5)-1].Time + 300, last: m5[len(m5)-1].Close, bars: append([]market.Candle(nil), m5...)}
	s.bar(4107.0, 0.3, 0.3) // k12
	s.bar(4107.4, 0.3, 0.3) // k13
	s.bar(4107.2, 0.3, 0.3) // k14
	for age, asOf := range []int{idx.shift, idx.shift + 1, idx.shift + 2, idx.shift + 3} {
		a := evalAt(t, testConfig(), h1, s.bars, asOf)
		want := age <= testConfig().ConfirmationMaxAgeBars
		if got := len(a.Setups) == 1; got != want {
			t.Fatalf("age %d: setup present = %v, want %v (%+v)", age, got, want, a)
		}
		if want && a.Setups[0].ConfirmationAge != age {
			t.Fatalf("age %d reported as %d", age, a.Setups[0].ConfirmationAge)
		}
	}
}

func TestThesisInvalidatedAfterConfirmationIsRejected(t *testing.T) {
	m5, h1, idx := bullishCRT()
	s := &scribe{t: m5[len(m5)-1].Time + 300, last: m5[len(m5)-1].Close, bars: append([]market.Candle(nil), m5...)}
	s.bar(4099.0, 0.3, 0.5) // k12 closes back below the H1 low
	a := evalAt(t, testConfig(), h1, s.bars, idx.shift+1)
	if len(a.Setups) != 0 || !hasRejection(a, ReasonInvalidatedAfterConfirm) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
}

func TestTargetAlreadyReachedBeforeConfirmationIsRejected(t *testing.T) {
	m5, h1 := bullishWith(func(s *scribe) {
		s.bar(4101.0, 0.3, 1.9)  // k9 sweep + reclaim
		s.bar(4103.0, 15.1, 0.3) // k10 wicks to 4118.1: touches the H1 high, no raid beyond the threshold
		s.bar(4106.4, 0.3, 0.2)  // k11 shift candle
	})
	a := Detect(testConfig(), Input{H1: h1, M5: m5})
	if len(a.Setups) != 0 || !hasRejection(a, ReasonTargetAlreadyReached) {
		t.Fatalf("setups %+v rejections %+v", a.Setups, a.Rejections)
	}
}

// The regression the frozen port fails: H1 and M5 positions were conflated, so
// the same M5 episode read differently depending on how many H1 candles were
// loaded. 400 H1 candles with only 150 M5 candles must read exactly like 160
// with 300 once the ATR windows are full (the trailing-window ATR makes the
// result independent of anything older than the window).
func TestInterpretationIsIndependentOfTheH1WarmupCount(t *testing.T) {
	baseM5, baseH1, _ := bullishCRTWith(150, 160)
	base := mustOneSetup(t, Detect(testConfig(), Input{H1: baseH1, M5: baseM5}))
	for _, c := range []struct{ fill, h1Count int }{{300, 400}, {150, 400}, {300, 160}, {138, 400}, {500, 150}} {
		m5, h1, _ := bullishCRTWith(c.fill, c.h1Count)
		got := mustOneSetup(t, Detect(testConfig(), Input{H1: h1, M5: m5}))
		if !reflect.DeepEqual(got, base) {
			t.Fatalf("fill %d / h1 %d changed the interpretation:\n got  %+v\n base %+v", c.fill, c.h1Count, got, base)
		}
	}
}

// Candles after the evaluated one are never read.
func TestPrefixInvarianceAndNoFutureLeakage(t *testing.T) {
	m5, h1, idx := bullishCRT()
	s := &scribe{t: m5[len(m5)-1].Time + 300, last: m5[len(m5)-1].Close, bars: append([]market.Candle(nil), m5...)}
	// Futures chosen to be adversarial: a sweep of the opposite edge and a
	// collapse below the stop. None of it may change a decision made earlier.
	s.bar(4121.0, 0.3, 0.2)
	s.bar(4090.0, 0.3, 0.2)
	s.bar(4101.0, 0.3, 1.0)
	for asOf := idx.first; asOf < len(s.bars); asOf++ {
		withFuture := detectAsOf(testConfig(), Input{H1: h1, M5: s.bars}, asOf)
		prefix := Detect(testConfig(), Input{H1: h1, M5: s.bars[:asOf+1]})
		if !reflect.DeepEqual(withFuture, prefix) {
			t.Fatalf("asOf %d: decision changed with future candles present:\n future %+v\n prefix %+v", asOf, withFuture, prefix)
		}
	}
	// And the shift candle's own decision is what it was.
	atShift := detectAsOf(testConfig(), Input{H1: h1, M5: s.bars}, idx.shift)
	if len(atShift.Setups) != 1 {
		t.Fatalf("future candles removed a setup that was confirmed at its time: %+v", atShift)
	}
}

func TestMalformedCandlesNeverProduceASetup(t *testing.T) {
	m5, h1, _ := bullishCRT()
	for name, mutate := range map[string]func([]market.Candle, []market.Candle){
		"duplicate M5 time": func(m, _ []market.Candle) { m[10].Time = m[9].Time },
		"high below low":    func(m, _ []market.Candle) { m[20].High = m[20].Low - 1 },
		"unordered H1":      func(_, h []market.Candle) { h[5].Time = h[6].Time + 1 },
	} {
		m, h := append([]market.Candle(nil), m5...), append([]market.Candle(nil), h1...)
		mutate(m, h)
		if a := Detect(testConfig(), Input{H1: h, M5: m}); len(a.Setups) != 0 || !hasRejection(a, ReasonInvalidCandles) {
			t.Fatalf("%s: %+v", name, a)
		}
	}
	if a := Detect(testConfig(), Input{H1: h1[:10], M5: m5}); !hasRejection(a, ReasonInsufficientH1History) {
		t.Fatalf("short H1 history: %+v", a)
	}
}
