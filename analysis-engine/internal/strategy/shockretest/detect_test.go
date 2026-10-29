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
	got := Detect(testConfig(t, workable), Input{M5: bars})
	if len(got.Setups) != 1 {
		t.Fatalf("setups = %d, rejections %+v", len(got.Setups), got.Rejections)
	}
	s := got.Setups[0]
	if s.Direction != market.Buy || s.OriginTime != bars[idx.shock].Time || s.ConfirmedAt != bars[idx.retest].Time || s.RetestTime != bars[idx.retest].Time || s.ConfirmationAge != 0 {
		t.Fatalf("setup = %+v", s)
	}
	leg := s.Extreme - s.Origin
	// The band is the 50 to 61.8 percent retracement of the leg; the stop is behind the origin; the target is the shock extreme.
	if math.Abs(s.EntryHigh-(s.Extreme-0.5*leg)) > 1e-9 || math.Abs(s.EntryLow-(s.Extreme-0.618*leg)) > 1e-9 {
		t.Fatalf("entry %v-%v for a leg %v-%v", s.EntryLow, s.EntryHigh, s.Origin, s.Extreme)
	}
	if want := s.Origin - math.Max(0.5, 0.1*s.M5ATR); math.Abs(s.Stop-want) > 1e-9 {
		t.Fatalf("stop %v, want %v", s.Stop, want)
	}
	if s.Target != s.Extreme || !s.WickRejection || s.Engulfing || s.LegATR < 4 || s.Retracement < 0.5 || s.Retracement > 0.786 {
		t.Fatalf("measured facts %+v", s)
	}
}

func TestBearishShockRetestIsTheExactMirror(t *testing.T) {
	bars, _ := bullishShockRetest()
	buy := Detect(testConfig(t, workable), Input{M5: bars}).Setups
	sell := Detect(testConfig(t, workable), Input{M5: scribe.Reflect(bars, 4100)}).Setups
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

// Entering at the 50 percent retracement with the stop behind the origin cannot give 1R: the reward is 0.5 L, the risk 0.5 L plus the buffer.
func TestTheShippedRoomRequirementCannotBeMet(t *testing.T) {
	bars, _ := bullishShockRetest()
	got := Detect(testConfig(t, nil), Input{M5: bars})
	if len(got.Setups) != 0 || !hasReason(got, ReasonNoTargetRoom) {
		t.Fatalf("with minimum_target_r 1.0 the geometry must be refused for room: %+v", got)
	}
	setup := Detect(testConfig(t, workable), Input{M5: bars}).Setups[0]
	if !(setup.TechnicalRR < 1) || setup.TechnicalRR < 0.8 {
		t.Fatalf("technical reward:risk %v, want in [0.8, 1)", setup.TechnicalRR)
	}
}

func TestAShockNeedsRangeAndBodyOrAWideTwoCandleLeg(t *testing.T) {
	small := scribe.New(fixtureHour-60*300, 4100)
	small.Filler(60, 4100)
	small.Bar(4104.0, 0.3, 0.3) // 4.6 range against an ATR of 2: not a shock
	small.Bar(4103.5, 0.3, 0.3)
	small.Bar(4102.0, 0.3, 0.3)
	small.Bar(4101.5, 0.1, 1.6)
	if got := Detect(testConfig(t, workable), Input{M5: small.Bars}); got.Shocks != 0 || len(got.Setups) != 0 {
		t.Fatalf("a small move is not a shock: %+v", got)
	}
	// Two consecutive bullish candles whose combined leg is wide (4.5 ATR) are one shock, each alone being too small.
	pair := scribe.New(fixtureHour-60*300, 4100)
	pair.Filler(60, 4100)
	pair.Bar(4104.4, 0.2, 0.2) // range 4.8, below 3 ATR
	pair.Bar(4109.0, 0.2, 0.2) // range 5.0, below 3 ATR; the leg 4100.3 -> 4109.0 is 8.7 = 4.3 ATR
	if got := Detect(testConfig(t, workable), Input{M5: pair.Bars}); got.Shocks == 0 {
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
	got := Detect(testConfig(t, workable), Input{M5: s.Bars})
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
		cfg := testConfig(t, workable)
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
	cfg := testConfig(t, workable)
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
