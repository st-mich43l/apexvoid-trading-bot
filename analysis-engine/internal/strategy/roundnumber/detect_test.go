package roundnumber

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

func TestBullishStopRunIsAnAcceptedBuy(t *testing.T) {
	bars, idx := bullishStopRun()
	got := Detect(testConfig(t, nil), Input{M5: bars})
	if len(got.Setups) != 1 {
		t.Fatalf("setups = %d, rejections %+v", len(got.Setups), got.Rejections)
	}
	s := got.Setups[0]
	if s.Kind != KindSweepReclaim || s.Direction != market.Buy || s.Handle != 4110 || s.Major {
		t.Fatalf("setup = %+v", s)
	}
	if s.SweepTime != bars[idx.sweep].Time || s.ConfirmedAt != bars[idx.shift].Time || s.ConfirmationAge != 0 {
		t.Fatalf("timing %d %d age %d", s.SweepTime, s.ConfirmedAt, s.ConfirmationAge)
	}
	// Entry band: the retest of the reclaimed handle, 15 pips wide above it; the stop beyond the manipulation extreme 4109.0.
	if s.EntryLow != 4110 || math.Abs(s.EntryHigh-4111.5) > 1e-9 {
		t.Fatalf("entry %v-%v", s.EntryLow, s.EntryHigh)
	}
	if !(s.Stop < 4109.0) || math.Abs(s.Stop-(4109.0-math.Max(0.5, 0.1*s.M5ATR))) > 1e-9 {
		t.Fatalf("stop %v (ATR %v)", s.Stop, s.M5ATR)
	}
	if s.Target != 4120 || s.TargetKind != TargetNextHandle {
		t.Fatalf("target %v %s", s.Target, s.TargetKind)
	}
	if !s.WickRejection || !s.Structural || math.Abs(s.RiskPips-(s.EntryHigh-s.Stop)/0.1) > 1e-9 || s.TechnicalRR < 1 {
		t.Fatalf("measured facts %+v", s)
	}
}

func TestBearishStopRunIsTheExactMirror(t *testing.T) {
	bars, _ := bullishStopRun()
	buy := Detect(testConfig(t, nil), Input{M5: bars}).Setups
	sell := Detect(testConfig(t, nil), Input{M5: scribe.Reflect(bars, 4110)}).Setups
	if len(buy) != 1 || len(sell) != 1 {
		t.Fatalf("buy %d sell %d", len(buy), len(sell))
	}
	b, s := buy[0], sell[0]
	if s.Direction != market.Sell || s.Handle != 4110 {
		t.Fatalf("sell = %+v", s)
	}
	if math.Abs(s.EntryHigh-(8220-b.EntryLow)) > 1e-9 || math.Abs(s.EntryLow-(8220-b.EntryHigh)) > 1e-9 || math.Abs(s.Stop-(8220-b.Stop)) > 1e-9 || math.Abs(s.Target-(8220-b.Target)) > 1e-9 {
		t.Fatalf("mirror broken: buy %+v sell %+v", b, s)
	}
	if s.RiskPips != b.RiskPips || s.TechnicalRR != b.TechnicalRR {
		t.Fatalf("magnitudes differ: %v/%v %v/%v", s.RiskPips, b.RiskPips, s.TechnicalRR, b.TechnicalRR)
	}
}

// bullishBreakRetest: price below the 4110 handle breaks up through it with a force candle, is accepted by two closes above, retests the band and
// rejects with a long lower wick.
func bullishBreakRetest() (bars []market.Candle, idx breakIndex) {
	s := scribe.New(fixtureHour-60*300, 4107)
	s.Filler(60, 4107)
	s.Bar(4109.0, 0.3, 0.3)
	idx.brk = s.Index()
	s.Bar(4112.4, 0.2, 0.2) // break: closes 24 pips above the handle, body 0.89 of a 3.8 range
	s.Bar(4112.8, 0.3, 0.3) // acceptance 1
	s.Bar(4112.0, 0.3, 0.3) // acceptance 2
	idx.retest = s.Index()
	s.Bar(4112.6, 0.1, 1.6) // retest: low 4110.4 inside the band, closes above its top with a lower wick of 0.7
	return s.Bars, idx
}

type breakIndex struct{ brk, retest int }

func TestBullishBreakRetestIsAnAcceptedBuy(t *testing.T) {
	bars, idx := bullishBreakRetest()
	got := Detect(testConfig(t, nil), Input{M5: bars})
	if len(got.Setups) != 1 {
		t.Fatalf("setups = %d, rejections %+v", len(got.Setups), got.Rejections)
	}
	s := got.Setups[0]
	if s.Kind != KindBreakRetest || s.Direction != market.Buy || s.Handle != 4110 {
		t.Fatalf("setup = %+v", s)
	}
	if s.BreakTime != bars[idx.brk].Time || s.RetestTime != bars[idx.retest].Time || s.ConfirmedAt != bars[idx.retest].Time {
		t.Fatalf("timing %+v", s)
	}
	if math.Abs(s.EntryLow-4109.5) > 1e-9 || math.Abs(s.EntryHigh-4111.5) > 1e-9 {
		t.Fatalf("entry %v-%v", s.EntryLow, s.EntryHigh)
	}
	// Stop: beyond the lower of the retest low and the handle minus the stop buffer (4109.5), by the larger of 5 pips and 0.10 ATR.
	if math.Abs(s.Stop-4109.0) > 1e-9 {
		t.Fatalf("stop %v", s.Stop)
	}
	if !s.WickRejection || s.Engulfing || s.Structural || s.ForceATR < 1 {
		t.Fatalf("measured facts %+v", s)
	}
	if s.Target != 4120 || s.TargetKind != TargetNextHandle {
		t.Fatalf("target %v %s", s.Target, s.TargetKind)
	}
}

func TestBearishBreakRetestIsTheExactMirror(t *testing.T) {
	bars, _ := bullishBreakRetest()
	buy := Detect(testConfig(t, nil), Input{M5: bars}).Setups
	sell := Detect(testConfig(t, nil), Input{M5: scribe.Reflect(bars, 4110)}).Setups
	if len(buy) != 1 || len(sell) != 1 {
		t.Fatalf("buy %d sell %d", len(buy), len(sell))
	}
	b, s := buy[0], sell[0]
	if s.Direction != market.Sell || math.Abs(s.EntryHigh-(8220-b.EntryLow)) > 1e-9 || math.Abs(s.Stop-(8220-b.Stop)) > 1e-9 || math.Abs(s.Target-(8220-b.Target)) > 1e-9 {
		t.Fatalf("mirror broken: buy %+v sell %+v", b, s)
	}
}

func TestAMajorHandleIsMarked(t *testing.T) {
	bars, _ := bullishStopRun()
	shifted := make([]market.Candle, len(bars))
	for i, c := range bars {
		shifted[i] = market.Candle{Time: c.Time, Open: c.Open + 40, High: c.High + 40, Low: c.Low + 40, Close: c.Close + 40}
	}
	got := Detect(testConfig(t, nil), Input{M5: shifted})
	if len(got.Setups) != 1 || got.Setups[0].Handle != 4150 || !got.Setups[0].Major {
		t.Fatalf("setups %+v rejections %+v", got.Setups, got.Rejections)
	}
}
