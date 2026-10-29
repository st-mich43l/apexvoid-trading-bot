package asiarange

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

func reflectM15(bars []market.Candle) []market.Candle { return scribe.Reflect(bars, asiaMid) }

func TestBullishRaidOfTheAsiaLowIsAnAcceptedBuy(t *testing.T) {
	m5, m15, idx := bullishRaid()
	got := Detect(testConfig(t, nil), Input{M5: m5, M15: m15, Anchor: anchorInput()})
	if len(got.Setups) != 1 {
		t.Fatalf("setups = %d, rejections %+v", len(got.Setups), got.Rejections)
	}
	s := got.Setups[0]
	if s.Kind != KindSweepReclaim || s.Direction != market.Buy || s.Edge != EdgeLow {
		t.Fatalf("setup = %+v", s)
	}
	if s.SweepTime != m5[idx.sweep].Time || s.ConfirmedAt != m5[idx.shift].Time || s.ConfirmationAge != 0 {
		t.Fatalf("timing %+v", s)
	}
	// The entry band is centred on the reclaimed edge (20 pips wide); the stop is beyond the manipulation extreme 4098.6.
	if math.Abs(s.EntryLow-4099) > 1e-9 || math.Abs(s.EntryHigh-4101) > 1e-9 {
		t.Fatalf("entry %v-%v", s.EntryLow, s.EntryHigh)
	}
	if want := 4098.6 - math.Max(0.5, 0.1*s.M5ATR); math.Abs(s.Stop-want) > 1e-9 {
		t.Fatalf("stop %v, want %v", s.Stop, want)
	}
	if s.Target != asiaMid || s.TargetKind != TargetRangeMid {
		t.Fatalf("target %v %s", s.Target, s.TargetKind)
	}
	if !s.WickRejection || !s.Structural || s.TechnicalRR < 1 || s.Anchor.RangeATR < 3 || s.Anchor.RangeATR > 4 {
		t.Fatalf("measured facts %+v", s)
	}
}

func TestBearishRaidOfTheAsiaHighIsTheExactMirror(t *testing.T) {
	m5, m15, _ := bullishRaid()
	buy := Detect(testConfig(t, nil), Input{M5: m5, M15: m15, Anchor: anchorInput()}).Setups
	sell := Detect(testConfig(t, nil), Input{M5: scribe.Reflect(m5, asiaMid), M15: reflectM15(m15), Anchor: anchorInput()}).Setups
	if len(buy) != 1 || len(sell) != 1 {
		t.Fatalf("buy %d sell %d", len(buy), len(sell))
	}
	b, s := buy[0], sell[0]
	if s.Direction != market.Sell || s.Edge != EdgeHigh || math.Abs(s.EntryHigh-(2*asiaMid-b.EntryLow)) > 1e-9 || math.Abs(s.Stop-(2*asiaMid-b.Stop)) > 1e-9 || math.Abs(s.Target-(2*asiaMid-b.Target)) > 1e-9 {
		t.Fatalf("mirror broken: buy %+v sell %+v", b, s)
	}
	if s.RiskPips != b.RiskPips || s.TechnicalRR != b.TechnicalRR {
		t.Fatal("magnitudes differ")
	}
}

func TestBullishAcceptanceOfTheAsiaHighIsAnAcceptedBuy(t *testing.T) {
	m5, m15, idx := bullishAcceptance()
	got := Detect(testConfig(t, nil), Input{M5: m5, M15: m15, Anchor: anchorInput()})
	if len(got.Setups) != 1 {
		t.Fatalf("setups = %d, rejections %+v", len(got.Setups), got.Rejections)
	}
	s := got.Setups[0]
	if s.Kind != KindBreakRetest || s.Direction != market.Buy || s.Edge != EdgeHigh {
		t.Fatalf("setup = %+v", s)
	}
	if s.BreakTime != m5[idx.brk].Time || s.RetestTime != m5[idx.retest].Time || s.ConfirmedAt != m5[idx.retest].Time {
		t.Fatalf("timing %+v", s)
	}
	if math.Abs(s.EntryLow-4129) > 1e-9 || math.Abs(s.EntryHigh-4131) > 1e-9 {
		t.Fatalf("entry %v-%v", s.EntryLow, s.EntryHigh)
	}
	// Stop: behind the retest low 4128.9 or the edge, whichever is further, and its buffer; the measured move L + 0.5 x 30 is the objective.
	if want := 4128.9 - math.Max(0.5, 0.1*s.M5ATR); math.Abs(s.Stop-want) > 1e-9 {
		t.Fatalf("stop %v, want %v", s.Stop, want)
	}
	if s.TargetKind != TargetMeasuredMove || math.Abs(s.Target-(asiaHigh+15)) > 1e-9 {
		t.Fatalf("target %v %s", s.Target, s.TargetKind)
	}
	if !s.WickRejection || s.Engulfing || s.ForceATR <= 0 {
		t.Fatalf("measured facts %+v", s)
	}
}

func TestBearishAcceptanceOfTheAsiaLowIsTheExactMirror(t *testing.T) {
	m5, m15, _ := bullishAcceptance()
	buy := Detect(testConfig(t, nil), Input{M5: m5, M15: m15, Anchor: anchorInput()}).Setups
	sell := Detect(testConfig(t, nil), Input{M5: scribe.Reflect(m5, asiaMid), M15: reflectM15(m15), Anchor: anchorInput()}).Setups
	if len(buy) != 1 || len(sell) != 1 {
		t.Fatalf("buy %d sell %d", len(buy), len(sell))
	}
	b, s := buy[0], sell[0]
	if s.Direction != market.Sell || s.Edge != EdgeLow || math.Abs(s.EntryLow-(2*asiaMid-b.EntryHigh)) > 1e-9 || math.Abs(s.Stop-(2*asiaMid-b.Stop)) > 1e-9 || math.Abs(s.Target-(2*asiaMid-b.Target)) > 1e-9 {
		t.Fatalf("mirror broken: buy %+v sell %+v", b, s)
	}
}
