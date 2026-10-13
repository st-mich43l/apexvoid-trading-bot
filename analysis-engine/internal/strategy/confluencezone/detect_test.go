package confluencezone

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
)

func near(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

func TestTwoIndependentDemandStructuresConfirmedBySweepReclaim(t *testing.T) {
	cfg := testConfig(t, nil)
	a := Detect(cfg, input(rejection(), twoDemand(), buyPool(4125)))
	s := only(t, a)
	if s.Direction != market.Buy || s.Reaction.Pattern != reaction.TypeSweepReclaim {
		t.Fatalf("direction %s pattern %s", s.Direction, s.Reaction.Pattern)
	}
	// The entry is the overlap of A and B, not their 4100-4108 union.
	if !near(s.EntryLow, 4103) || !near(s.EntryHigh, 4105) {
		t.Fatalf("entry [%v, %v], want the overlap [4103, 4105]", s.EntryLow, s.EntryHigh)
	}
	// The stop protects the supporting structures (4100) and the reaction's own
	// adverse extreme (4102), whichever is further, minus the buffer (0.5 ATR).
	if !near(s.ProtectedFar, 4100) || !near(s.Reaction.Adverse, 4102) || !near(s.Stop, 4100-2.5) {
		t.Fatalf("far %v adverse %v stop %v", s.ProtectedFar, s.Reaction.Adverse, s.Stop)
	}
	if !near(s.Target, 4125) || !near(s.RewardRisk, 20.0/7.5) || !near(s.TargetRoomATR, 4) {
		t.Fatalf("target %v rr %v room %v", s.Target, s.RewardRisk, s.TargetRoomATR)
	}
	if s.IndependentFacts != 2 || s.DependencyCount != 0 || len(s.Support) != 2 {
		t.Fatalf("independent %d dependency %d support %d", s.IndependentFacts, s.DependencyCount, len(s.Support))
	}
	if s.Region.EpisodeID != structuralconfluence.EpisodeID(structuralconfluence.Buy, []string{"A", "B"}) {
		t.Fatalf("episode %s", s.Region.EpisodeID)
	}
	if s.TouchIndex != 29 || s.ConfirmIndex != 29 || s.Freshness != 0 {
		t.Fatalf("touch %d confirm %d fresh %d", s.TouchIndex, s.ConfirmIndex, s.Freshness)
	}
	// Measured, not asserted: a 2.5-point lower wick on a 4.0 range candle.
	if !near(s.Reaction.WickRatio, 0.625) || !near(s.Reaction.CloseStrength, 0.875) || !near(s.Reaction.SweepDepthATR, 0.2) {
		t.Fatalf("measured %+v", s.Reaction)
	}
}

func TestWickRejectionWithoutASweep(t *testing.T) {
	cfg := testConfig(t, nil)
	// The wick reaches the region (4103.8 is inside 4103-4105) without going through its low.
	a := Detect(cfg, input(withLast(4104.6, 4106, 4103.8, 4105.6), twoDemand(), buyPool(4125)))
	if s := only(t, a); s.Reaction.Pattern != reaction.TypeWickRejection || s.Reaction.SweepDepthATR != 0 {
		t.Fatalf("%+v", s.Reaction)
	}
}

func TestStrongReclaimIsADisplacementThroughTheRegion(t *testing.T) {
	cfg := testConfig(t, nil)
	// A bullish body of real range that closes above the region, with no wick.
	a := Detect(cfg, input(withLast(4104.8, 4108, 4104.6, 4107.8), twoDemand(), buyPool(4125)))
	s := only(t, a)
	if s.Reaction.Pattern != reaction.TypeStrongReclaim || s.Reaction.WickRatio > 0.1 {
		t.Fatalf("%+v", s.Reaction)
	}
}

func TestFreshRetestAfterTheStructuresWereEstablished(t *testing.T) {
	// The touch is two candles before the confirming candle: both inside the window.
	bars := approach()
	bars[28] = bar(28, 4107, 4107.2, 4104.2, 4104.6) // touches the region, closes inside it
	bars[29] = bar(29, 4104.6, 4107.4, 4104.4, 4107.1)
	a := Detect(testConfig(t, nil), input(bars, twoDemand(), buyPool(4125)))
	s := only(t, a)
	if s.TouchIndex != 29 && s.TouchIndex != 28 {
		t.Fatalf("touch %d", s.TouchIndex)
	}
	if s.ConfirmIndex != 29 {
		t.Fatalf("confirm %d", s.ConfirmIndex)
	}
}

func TestNearestValidOpposingLiquidityIsTheTarget(t *testing.T) {
	cfg := testConfig(t, nil)
	pools := []struct {
		levels []float64
		want   float64
	}{
		{[]float64{4130, 4125, 4140}, 4125},
		// One closer than the minimum distance (1 ATR = 5 above the entry high of
		// 4105) is not a target.
		{[]float64{4107, 4125}, 4125},
		// One at or below the price already traded since the touch is spent.
		{[]float64{4105.8, 4120}, 4120},
	}
	for _, c := range pools {
		in := input(rejection(), twoDemand())
		for _, l := range c.levels {
			in.Pools = append(in.Pools, buyPool(l))
		}
		// The member swings are recent (candle 28), so the earlier range does not sweep them.
		in.Swings = poolSwingsAt(28, in.Pools)
		if got := only(t, Detect(cfg, in)).Target; !near(got, c.want) {
			t.Fatalf("levels %v: target %v, want %v", c.levels, got, c.want)
		}
	}
}

func TestSupplyIsTheExactMirrorOfDemand(t *testing.T) {
	cfg := testConfig(t, nil)
	buy := only(t, Detect(cfg, input(rejection(), twoDemand(), buyPool(4125))))
	const axis = 8300.0 // reflect every price about 4150: p -> axis - p
	mirror := func(p float64) float64 { return axis - p }
	var bars []market.Candle
	for _, b := range rejection() {
		bars = append(bars, market.Candle{Time: b.Time, Open: mirror(b.Open), High: mirror(b.Low), Low: mirror(b.High), Close: mirror(b.Close), Volume: b.Volume})
	}
	var facts []structuralconfluence.Fact
	for _, f := range twoDemand() {
		m := f
		m.Side, m.Low, m.High = structuralconfluence.Sell, mirror(f.High), mirror(f.Low)
		m.StructuralLow, m.StructuralHigh = m.Low, m.High
		facts = append(facts, m)
	}
	in := input(bars, facts)
	in.Pools = append(in.Pools, poolSell(mirror(4125)))
	in.Swings = poolSwings(in.Pools)
	sell := only(t, Detect(cfg, in))
	if sell.Direction != market.Sell || sell.Reaction.Pattern != buy.Reaction.Pattern {
		t.Fatalf("%s %s", sell.Direction, sell.Reaction.Pattern)
	}
	checks := map[string][2]float64{
		"entry low": {sell.EntryLow, mirror(buy.EntryHigh)}, "entry high": {sell.EntryHigh, mirror(buy.EntryLow)},
		"stop": {sell.Stop, mirror(buy.Stop)}, "target": {sell.Target, mirror(buy.Target)},
		"far": {sell.ProtectedFar, mirror(buy.ProtectedFar)}, "adverse": {sell.Reaction.Adverse, mirror(buy.Reaction.Adverse)},
		"risk pips": {sell.RiskPips, buy.RiskPips}, "reward risk": {sell.RewardRisk, buy.RewardRisk},
		"wick": {sell.Reaction.WickRatio, buy.Reaction.WickRatio}, "close strength": {sell.Reaction.CloseStrength, buy.Reaction.CloseStrength},
	}
	for name, pair := range checks {
		if !near(pair[0], pair[1]) {
			t.Errorf("%s: sell %v, mirrored buy %v", name, pair[0], pair[1])
		}
	}
}

func TestThreeFactsOverTwoIndependentOrigins(t *testing.T) {
	facts := append(twoDemand(), fact("C", structuralconfluence.Buy, 4102, 4106, factsReady, "gap:b")) // C shares B's origin
	s := only(t, Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125))))
	if s.IndependentFacts != 2 || s.DependencyCount != 1 || len(s.Support) != 3 {
		t.Fatalf("independent %d dependency %d support %d", s.IndependentFacts, s.DependencyCount, len(s.Support))
	}
}

func TestNestedIntersectionsUseTheSupportedRegion(t *testing.T) {
	facts := []structuralconfluence.Fact{
		fact("outer", structuralconfluence.Buy, 4098, 4110, factsReady, "o"),
		fact("mid", structuralconfluence.Buy, 4101, 4106, factsReady, "m"),
		fact("inner", structuralconfluence.Buy, 4103, 4104, factsReady, "i"),
	}
	s := only(t, Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125))))
	// outer and mid support 4101-4106 everywhere with two independent sources.
	if !near(s.Region.Low, 4101) || !near(s.Region.High, 4106) {
		t.Fatalf("region [%v, %v]", s.Region.Low, s.Region.High)
	}
}

func TestSeveralDisjointRegionsOnlyTheTouchedOneConfirms(t *testing.T) {
	facts := append(twoDemand(),
		fact("C", structuralconfluence.Buy, 4060, 4065, factsReady, "c"),
		fact("D", structuralconfluence.Buy, 4062, 4068, factsReady, "d"))
	a := Detect(testConfig(t, nil), input(rejection(), facts, buyPool(4125)))
	if len(a.Regions) != 2 {
		t.Fatalf("regions %+v", a.Regions)
	}
	if s := only(t, a); !near(s.Region.Low, 4103) {
		t.Fatalf("region %v", s.Region)
	}
}

func TestXAUAndFXPrecision(t *testing.T) {
	// The same geometry at EURUSD scale: pips are 0.0001 and prices ~1.1.
	cfg := testConfig(t, map[string]any{"pip_size": 0.0001, "fvg_entry_max_width_price": 0.0050})
	scale := func(p float64) float64 { return 1.10 + (p-4100)*0.0001 }
	var bars []market.Candle
	for _, b := range rejection() {
		bars = append(bars, market.Candle{Time: b.Time, Open: scale(b.Open), High: scale(b.High), Low: scale(b.Low), Close: scale(b.Close), Volume: 1})
	}
	facts := []structuralconfluence.Fact{
		fact("A", structuralconfluence.Buy, scale(4100), scale(4105), factsReady, "a"),
		fact("B", structuralconfluence.Buy, scale(4103), scale(4108), factsReady, "b"),
	}
	in := input(bars, facts, buyPool(scale(4125)))
	in.ATR = 0.0005
	s := only(t, Detect(cfg, in))
	// 7.5 price units on the XAU scale are 75 pips there and 7.5 pips here.
	if math.Abs(s.RiskPips-7.5) > 1e-6 || math.Abs(s.OverlapWidthPips-2) > 1e-6 {
		t.Fatalf("risk pips %v overlap pips %v", s.RiskPips, s.OverlapWidthPips)
	}
}
