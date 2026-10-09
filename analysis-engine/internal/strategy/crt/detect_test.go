package crt

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func near(a, b, tol float64) bool { return math.Abs(a-b) <= tol }

func mustOneSetup(t *testing.T, a Analysis) Setup {
	t.Helper()
	if len(a.Setups) != 1 {
		t.Fatalf("want exactly one setup, got %d (rejections %+v)", len(a.Setups), a.Rejections)
	}
	return a.Setups[0]
}

func hasRejection(a Analysis, reason string) bool {
	for _, r := range a.Rejections {
		if r.Reason == reason {
			return true
		}
	}
	return false
}

func TestBullishSweepReclaimAndStructureShiftIsAccepted(t *testing.T) {
	m5, h1, idx := bullishCRT()
	a := Detect(testConfig(), Input{H1: h1, M5: m5})
	s := mustOneSetup(t, a)

	if s.Direction != market.Buy {
		t.Fatalf("direction = %s", s.Direction)
	}
	anchorOpen := fixtureHour
	if s.Anchor.OpenTime != anchorOpen || s.Anchor.CloseTime != anchorOpen+3600 || s.Anchor.High != 4118 || s.Anchor.Low != 4100 {
		t.Fatalf("anchor = %+v", s.Anchor)
	}
	// Wilder ATR(14) of the H1 series at the anchor: ten flat 10.0 ranges, then
	// TR 18 -> (13*10 + 18)/14.
	if !near(s.Anchor.ATR, 148.0/14, 1e-9) || !near(s.Anchor.RangeATR, 18/(148.0/14), 1e-9) {
		t.Fatalf("anchor ATR %v ratio %v", s.Anchor.ATR, s.Anchor.RangeATR)
	}
	if s.Sweep.BarTime != m5[idx.sweep].Time || !near(s.Sweep.ExtremePrice, 4098.6, 1e-9) || !near(s.Sweep.Depth, 1.4, 1e-9) {
		t.Fatalf("sweep = %+v", s.Sweep)
	}
	if s.Reclaim.BarTime != m5[idx.reclaim].Time || s.Reclaim.BarsAfterSweep != 0 || !near(s.Reclaim.Close, 4101.0, 1e-9) {
		t.Fatalf("reclaim = %+v", s.Reclaim)
	}
	if s.Shift == nil || s.Shift.BarTime != m5[idx.shift].Time || !near(s.Shift.Level, 4105.6, 1e-9) || s.Shift.LevelTime != m5[idx.k(5)].Time {
		t.Fatalf("shift = %+v", s.Shift)
	}
	if s.ConfirmedAt != m5[idx.shift].Time || s.ConfirmationAge != 0 {
		t.Fatalf("confirmed at %d age %d", s.ConfirmedAt, s.ConfirmationAge)
	}
	// The stop sits beyond the actual sweep extreme, never inside the wick.
	if !(s.Stop < s.Sweep.ExtremePrice) {
		t.Fatalf("stop %v is not beyond the sweep extreme %v", s.Stop, s.Sweep.ExtremePrice)
	}
	if !near(s.Stop, 4098.6-0.25*s.M5ATR, 1e-9) {
		t.Fatalf("stop %v, want extreme minus 0.25 ATR (%v)", s.Stop, 4098.6-0.25*s.M5ATR)
	}
	// The objective is the opposite H1 edge.
	if s.Target != 4118 {
		t.Fatalf("target = %v", s.Target)
	}
	// Retest of the broken swing: band [swing - 0.5 ATR, swing + 0.1 ATR].
	if !near(s.EntryHigh, 4105.6+0.1*s.M5ATR, 1e-9) || !near(s.EntryLow, 4105.6-0.5*s.M5ATR, 1e-9) {
		t.Fatalf("entry = [%v, %v] ATR %v", s.EntryLow, s.EntryHigh, s.M5ATR)
	}
	risk, reward := s.EntryHigh-s.Stop, s.Target-s.EntryHigh
	if !near(s.RiskPips, risk/0.1, 1e-6) || !near(s.RewardPips, reward/0.1, 1e-6) || !near(s.TechnicalRR, reward/risk, 1e-9) {
		t.Fatalf("risk %v reward %v rr %v", s.RiskPips, s.RewardPips, s.TechnicalRR)
	}
	if !s.WickRejection {
		t.Fatal("the sweep candle is a wick rejection")
	}
	if s.Shift.BodyRatio < 0.8 || s.Shift.DisplacementATR < 1.4 || s.Shift.CloseStrength < 0.9 {
		t.Fatalf("shift measurements = %+v", s.Shift)
	}
}

func TestBearishCRTIsTheExactMirrorOfTheBullishOne(t *testing.T) {
	m5, h1, _ := bullishCRT()
	bull := mustOneSetup(t, Detect(testConfig(), Input{H1: h1, M5: m5}))

	center := 4110.0
	bear := mustOneSetup(t, Detect(testConfig(), Input{H1: reflectPrices(h1, center), M5: reflectPrices(m5, center)}))
	if bear.Direction != market.Sell {
		t.Fatalf("direction = %s", bear.Direction)
	}
	r := func(p float64) float64 { return 2*center - p }
	checks := map[string][2]float64{
		"anchor high": {bear.Anchor.High, r(bull.Anchor.Low)}, "anchor low": {bear.Anchor.Low, r(bull.Anchor.High)},
		"extreme": {bear.Sweep.ExtremePrice, r(bull.Sweep.ExtremePrice)}, "stop": {bear.Stop, r(bull.Stop)}, "target": {bear.Target, r(bull.Target)},
		"entry low": {bear.EntryLow, r(bull.EntryHigh)}, "entry high": {bear.EntryHigh, r(bull.EntryLow)},
		"shift level": {bear.Shift.Level, r(bull.Shift.Level)}, "rr": {bear.TechnicalRR, bull.TechnicalRR},
		"risk": {bear.RiskPips, bull.RiskPips}, "sweep depth": {bear.Sweep.Depth, bull.Sweep.Depth},
	}
	for name, pair := range checks {
		if !near(pair[0], pair[1], 1e-9) {
			t.Errorf("%s: sell %v, mirrored buy %v", name, pair[0], pair[1])
		}
	}
	if bear.ConfirmedAt != bull.ConfirmedAt || bear.Sweep.BarTime != bull.Sweep.BarTime {
		t.Fatalf("times differ: %+v vs %+v", bear, bull)
	}
	// A sell's stop is above its sweep extreme, and its target is below entry.
	if !(bear.Stop > bear.Sweep.ExtremePrice) || !(bear.Target < bear.EntryLow) {
		t.Fatalf("sell geometry: stop %v extreme %v target %v entry low %v", bear.Stop, bear.Sweep.ExtremePrice, bear.Target, bear.EntryLow)
	}
}

func TestReclaimRetestEntryModelEntersAtTheReclaimedEdge(t *testing.T) {
	m5, h1, _ := bullishCRT()
	cfg := testConfig()
	cfg.EntryModel = EntryReclaimRetest
	s := mustOneSetup(t, Detect(cfg, Input{H1: h1, M5: m5}))
	// A band centred on the reclaimed H1 low, 0.1 ATR either side.
	tol := 0.1 * s.M5ATR
	if !near(s.EntryLow, 4100-tol, 1e-9) || !near(s.EntryHigh, 4100+tol, 1e-9) {
		t.Fatalf("entry = [%v, %v], want 4100 +/- %v", s.EntryLow, s.EntryHigh, tol)
	}
	if !(s.Stop < s.Sweep.ExtremePrice) {
		t.Fatalf("stop %v not beyond extreme %v", s.Stop, s.Sweep.ExtremePrice)
	}
	// Risk is bounded by the sweep depth plus the buffer and the band, far below
	// the retest of a swing 5 above the edge.
	mss := mustOneSetup(t, Detect(testConfig(), Input{H1: h1, M5: m5}))
	if !(s.RiskPips < mss.RiskPips) {
		t.Fatalf("reclaim_retest risk %.1f pips should be below mss_retest %.1f", s.RiskPips, mss.RiskPips)
	}
}

func TestSweepReclaimBaselineModeConfirmsOnTheReclaimAlone(t *testing.T) {
	m5, h1, idx := bullishCRT()
	cfg := testConfig()
	cfg.ConfirmationMode, cfg.EntryModel = ConfirmationSweepReclaim, EntryReclaimRetest
	// Evaluate on the reclaim candle itself: no structure shift exists yet.
	a := detectAsOf(cfg, Input{H1: h1, M5: m5}, idx.reclaim)
	s := mustOneSetup(t, a)
	if s.Shift != nil || s.ConfirmedAt != m5[idx.reclaim].Time {
		t.Fatalf("baseline setup = %+v", s)
	}
	// The strict contract must not confirm on that same candle.
	if strict := detectAsOf(testConfig(), Input{H1: h1, M5: m5}, idx.reclaim); len(strict.Setups) != 0 {
		t.Fatalf("mss mode confirmed on the reclaim alone: %+v", strict.Setups)
	}
}
