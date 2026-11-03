package shockretest

import (
	"math"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/scribe"
)

func hasReason(a Analysis, reason string) bool {
	for _, r := range a.Rejections {
		if r.Reason == reason {
			return true
		}
	}
	return false
}

func TestBullishShockRetestIsAnAcceptedBuy(t *testing.T) {
	bars, idx := bullishShockRetest()
	got := Detect(testConfig(t, nil), Input{M5: bars})
	if len(got.Setups) != 1 {
		t.Fatalf("setups = %d, rejections %+v", len(got.Setups), got.Rejections)
	}
	s := got.Setups[0]
	if s.Direction != market.Buy || s.OriginTime != bars[idx.shock].Time || s.ConfirmedAt != bars[idx.retest].Time || s.RetestTime != bars[idx.retest].Time || s.ConfirmationAge != 0 {
		t.Fatalf("setup = %+v", s)
	}
	leg := s.Extreme - s.Origin
	// The band is the 50 to 61.8 percent retracement of the leg; the stop is behind the retest's rejection extreme, clamped to the band's far edge when
	// that extreme is above it (here 4104.4 against a far edge of 4104.12); the target is the shock extreme.
	if math.Abs(s.EntryHigh-(s.Extreme-0.5*leg)) > 1e-9 || math.Abs(s.EntryLow-(s.Extreme-0.618*leg)) > 1e-9 {
		t.Fatalf("entry %v-%v for a leg %v-%v", s.EntryLow, s.EntryHigh, s.Origin, s.Extreme)
	}
	if !(s.RetestExtreme > s.EntryLow) {
		t.Fatalf("fixture: the retest extreme %v must sit above the band's far edge %v", s.RetestExtreme, s.EntryLow)
	}
	if want := s.EntryLow - math.Max(0.5, 0.1*s.M5ATR); math.Abs(s.Stop-want) > 1e-9 {
		t.Fatalf("stop %v, want the far edge minus the buffer %v", s.Stop, want)
	}
	if s.Target != s.Extreme || s.TargetKind != TargetShockExtreme || s.TechnicalRR < 1 || !s.WickRejection || s.Engulfing || s.LegATR < 4 || s.Retracement < 0.5 || s.Retracement > 0.786 {
		t.Fatalf("measured facts %+v", s)
	}
}

func TestBearishShockRetestIsTheExactMirror(t *testing.T) {
	bars, _ := bullishShockRetest()
	buy := Detect(testConfig(t, nil), Input{M5: bars}).Setups
	sell := Detect(testConfig(t, nil), Input{M5: scribe.Reflect(bars, 4100)}).Setups
	if len(buy) != 1 || len(sell) != 1 {
		t.Fatalf("buy %d sell %d", len(buy), len(sell))
	}
	b, s := buy[0], sell[0]
	if s.Direction != market.Sell || math.Abs(s.EntryHigh-(8200-b.EntryLow)) > 1e-9 || math.Abs(s.Stop-(8200-b.Stop)) > 1e-9 || math.Abs(s.Target-(8200-b.Target)) > 1e-9 {
		t.Fatalf("mirror broken: buy %+v sell %+v", b, s)
	}
	if s.RiskPips != b.RiskPips || s.TechnicalRR != b.TechnicalRR {
		t.Fatal("magnitudes differ")
	}
}

// A deep wick: the retest's lowest low is 4099.9, just above the leg origin (4099.7: the last filler close), so the stop is far below the entry. The shock
// extreme then leaves less than 1R (5.3 of room against a risk of 5.6) and the target is the 1.272 extension of the leg.
func deepWickRetest() []market.Candle {
	s := scribe.New(fixtureHour-60*300, 4100)
	s.Filler(60, 4100)
	s.Bar(4110.0, 0.3, 0.3)
	s.Bar(4109.5, 0.3, 0.3)
	s.Bar(4108.0, 0.3, 0.3)
	s.Bar(4106.0, 0.2, 0.3)
	s.Bar(4106.4, 0.1, 6.1) // low 4099.9, closes up with a lower wick of 6.1
	return s.Bars
}

func TestWhenTheExtremeLeavesTooLittleRoomTheTargetIsTheExtension(t *testing.T) {
	bars := deepWickRetest()
	got := Detect(testConfig(t, nil), Input{M5: bars})
	if len(got.Setups) != 1 {
		t.Fatalf("setups = %d, rejections %+v", len(got.Setups), got.Rejections)
	}
	s := got.Setups[0]
	leg := s.Extreme - s.Origin
	if s.TargetKind != TargetShockExtension1272 || math.Abs(s.Target-(s.Origin+1.272*leg)) > 1e-9 {
		t.Fatalf("target %v (%s), want the 1.272 extension %v", s.Target, s.TargetKind, s.Origin+1.272*leg)
	}
	if math.Abs(s.RetestExtreme-4099.9) > 1e-9 || math.Abs(s.Stop-(4099.9-math.Max(0.5, 0.1*s.M5ATR))) > 1e-9 {
		t.Fatalf("the stop must sit behind the rejection extreme 4099.9: stop %v extreme %v", s.Stop, s.RetestExtreme)
	}
	if s.TechnicalRR < 1 {
		t.Fatalf("technical reward:risk %v", s.TechnicalRR)
	}
	// the same sequence mirrored is a SELL with the mirrored extension
	sell := Detect(testConfig(t, nil), Input{M5: scribe.Reflect(bars, 4100)}).Setups
	if len(sell) != 1 || sell[0].Direction != market.Sell || sell[0].TargetKind != TargetShockExtension1272 || math.Abs(sell[0].Target-(8200-s.Target)) > 1e-9 {
		t.Fatalf("mirror: %+v", sell)
	}
}

func TestTheShockExtremeIsTheTargetWhenItLeavesTheRoom(t *testing.T) {
	bars, _ := bullishShockRetest()
	for name, series := range map[string][]market.Candle{"BUY": bars, "SELL": scribe.Reflect(bars, 4100)} {
		got := Detect(testConfig(t, nil), Input{M5: series}).Setups
		if len(got) != 1 || got[0].TargetKind != TargetShockExtreme || got[0].TechnicalRR < 1 {
			t.Fatalf("%s: %+v", name, got)
		}
	}
}

func TestWithNeitherTargetOfferingTheRoomTheSetupIsRefused(t *testing.T) {
	got := Detect(testConfig(t, map[string]any{"minimum_target_r": 3.5}), Input{M5: deepWickRetest()})
	if len(got.Setups) != 0 || !hasReason(got, ReasonNoTargetRoom) {
		t.Fatalf("want no_target_room: %+v", got)
	}
}

func TestAStopNeverSitsInsideTheBand(t *testing.T) {
	for name, bars := range map[string][]market.Candle{"shallow": func() []market.Candle { b, _ := bullishShockRetest(); return b }(), "deep": deepWickRetest()} {
		for _, s := range Detect(testConfig(t, nil), Input{M5: bars}).Setups {
			if !(s.Stop < s.EntryLow) || !(s.Stop <= s.RetestExtreme) {
				t.Fatalf("%s: stop %v must be below the band %v-%v and the retest extreme %v", name, s.Stop, s.EntryLow, s.EntryHigh, s.RetestExtreme)
			}
		}
	}
}

func TestAShockNeedsRangeAndBodyOrAWideTwoCandleLeg(t *testing.T) {
	small := scribe.New(fixtureHour-60*300, 4100)
	small.Filler(60, 4100)
	small.Bar(4104.0, 0.3, 0.3) // 4.6 range against an ATR of 2: not a shock
	small.Bar(4103.5, 0.3, 0.3)
	small.Bar(4102.0, 0.3, 0.3)
	small.Bar(4101.5, 0.1, 1.6)
	if got := Detect(testConfig(t, nil), Input{M5: small.Bars}); got.Shocks != 0 || len(got.Setups) != 0 {
		t.Fatalf("a small move is not a shock: %+v", got)
	}
	// Two consecutive bullish candles whose combined leg is wide (4.5 ATR) are one shock, each alone being too small.
	pair := scribe.New(fixtureHour-60*300, 4100)
	pair.Filler(60, 4100)
	pair.Bar(4104.4, 0.2, 0.2) // range 4.8, below 3 ATR
	pair.Bar(4109.0, 0.2, 0.2) // range 5.0, below 3 ATR; the leg 4100.3 -> 4109.0 is 8.7 = 4.3 ATR
	if got := Detect(testConfig(t, nil), Input{M5: pair.Bars}); got.Shocks == 0 {
		t.Fatalf("a two-candle leg of 4.3 ATR must be a shock: %+v", got)
	}
}

func TestTheYardstickIsMeasuredBeforeTheShock(t *testing.T) {
	// A candle of 5.0 is 2.5 ATR of a quiet hour but only 1.5 of an hour already full of 10-point candles.
	quiet := scribe.New(fixtureHour-60*300, 4100)
	quiet.Filler(60, 4100)
	quiet.Bar(4105.0, 0, 0)
	if got := Detect(testConfig(t, map[string]any{"shock_range_atr": 2.4, "minimum_shock_body_ratio": 0.6}), Input{M5: quiet.Bars}); got.Shocks != 1 {
		t.Fatalf("shocks = %d", got.Shocks)
	}
}

func TestAnEpisodeEndsWithItsFirstOutcome(t *testing.T) {
	bars, _ := bullishShockRetest()
	// The shock continues before the pullback: the leg extends and the band follows the new extreme.
	s := scribe.New(fixtureHour-60*300, 4100)
	s.Filler(60, 4100)
	s.Bar(4110.0, 0.3, 0.3)
	s.Bar(4109.5, 0.3, 0.3) // settle
	s.Bar(4116.0, 0.3, 0.3) // the move continues (a second shock candle)
	s.Bar(4115.5, 0.3, 0.3) // settle again
	s.Bar(4113.0, 0.3, 0.3)
	s.Bar(4110.0, 0.2, 0.3)
	s.Bar(4110.4, 0.1, 1.9)
	got := Detect(testConfig(t, nil), Input{M5: s.Bars})
	_ = bars
	if len(got.Setups) > 1 {
		t.Fatalf("one move, one episode: %+v", got.Setups)
	}
	for _, setup := range got.Setups {
		if setup.Extreme < 4116 {
			t.Fatalf("the leg did not extend to the new extreme: %+v", setup)
		}
	}
}

func TestAppendingFutureCandlesNeverChangesADecisionAlreadyMade(t *testing.T) {
	bars, _ := bullishShockRetest()
	for name, series := range map[string][]market.Candle{"BUY": bars, "SELL": scribe.Reflect(bars, 4100)} {
		cfg := testConfig(t, nil)
		future := scribe.New(series[len(series)-1].Time+300, series[len(series)-1].Close)
		future.Bars = append(future.Bars, series...)
		future.Bar(4150.0, 1, 1)
		future.Bar(4050.0, 1, 1)
		for asOf := 40; asOf < len(series); asOf++ {
			got := DetectAsOf(cfg, Input{M5: future.Bars}, asOf)
			want := Detect(cfg, Input{M5: series[:asOf+1]})
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("%s at candle %d: the future changed the decision\n got  %+v\n want %+v", name, asOf, got, want)
			}
		}
	}
}

func TestBarByBarEvaluationPublishesTheThesisOnce(t *testing.T) {
	bars, idx := bullishShockRetest()
	cfg := testConfig(t, nil)
	published := 0
	for asOf := 40; asOf < len(bars); asOf++ {
		got := Detect(cfg, Input{M5: bars[:asOf+1]})
		if asOf < idx.retest && len(got.Setups) != 0 {
			t.Fatalf("a setup was published at candle %d, before its confirmation at %d", asOf, idx.retest)
		}
		published += len(got.Setups)
	}
	if published != 1 {
		t.Fatalf("published %d times, want once", published)
	}
}
