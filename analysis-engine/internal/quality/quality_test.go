package quality

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func TestPrimitivesAreBoundedMonotoneWhereTheyClaimToBeAndTreatMissingEvidenceAsZero(t *testing.T) {
	nan, inf := math.NaN(), math.Inf(1)
	for _, x := range []float64{nan, inf, -inf} {
		for name, got := range map[string]float64{
			"ramp": Ramp(x, 0, 1), "fall": Fall(x, 0, 1), "peak": Peak(x, 0, 1, 2, 3), "clamp": Clamp01(x), "room": Room(x, 1, true),
			"location": LocationScore(x, 1, 1), "close": CloseStrength(market.Candle{High: x, Low: 0, Close: 1}, true),
		} {
			if got != 0 {
				t.Errorf("%s(%v) = %v: missing evidence must score 0", name, x, got)
			}
		}
	}
	prevRamp, prevFall := -1.0, 2.0
	for x := -1.0; x <= 5; x += 0.25 {
		r, f, p := Ramp(x, 1, 3), Fall(x, 1, 3), Peak(x, 0, 1, 2, 4)
		if r < prevRamp || f > prevFall {
			t.Fatalf("Ramp must not fall and Fall must not rise: x=%v ramp=%v fall=%v", x, r, f)
		}
		prevRamp, prevFall = r, f
		for _, v := range []float64{r, f, p} {
			if v < 0 || v > 1 {
				t.Fatalf("value %v outside [0, 1] at x=%v", v, x)
			}
		}
	}
	if Peak(0.5, 0, 1, 2, 4) != 0.5 || Peak(1.5, 0, 1, 2, 4) != 1 || Peak(3, 0, 1, 2, 4) != 0.5 || Peak(4, 0, 1, 2, 4) != 0 {
		t.Fatalf("Peak is not the trapezoid it documents")
	}
	if Peak(1, 2, 1, 3, 4) != 0 || Ramp(1, 2, 2) != 0 {
		t.Fatalf("a malformed range is missing evidence, not a score")
	}
}

func TestMoreIsNotAlwaysBetter(t *testing.T) {
	// A level tested once is a price, three or four times a level, and many times a worn-out one.
	if !(Peak(1, 1, 3, 5, 9) < Peak(3, 1, 3, 5, 9) && Peak(5, 1, 3, 5, 9) == Peak(3, 1, 3, 5, 9) && Peak(8, 1, 3, 5, 9) < Peak(5, 1, 3, 5, 9)) {
		t.Fatalf("the touch curve must rise, plateau and fall")
	}
	// An entry that price has already left is worse than one at the edge.
	if !(LocationScore(100, 100, 1) > LocationScore(101, 100, 1) && LocationScore(101, 100, 1) > LocationScore(102, 100, 1)) {
		t.Fatalf("a late entry must score lower")
	}
}

func TestRoomNeverPaysForAFixedRatioTarget(t *testing.T) {
	if Room(30, 10, false) != 0 {
		t.Fatalf("a fixed reward:risk target is not structure")
	}
	if Room(10, 10, true) != 0 || Room(30, 10, true) != 1 || math.Abs(Room(20, 10, true)-0.5) > 1e-12 {
		t.Fatalf("room is 1R -> 0, 3R -> 1")
	}
	if Room(30, 0, true) != 0 || Room(-5, 10, true) != 0 {
		t.Fatalf("no risk or no reward is no room")
	}
}

func TestEpisodesCountVisitsNotBars(t *testing.T) {
	bar := func(o, h, l, c float64) market.Candle { return market.Candle{Open: o, High: h, Low: l, Close: c} }
	grind := []market.Candle{bar(100, 101, 99.5, 100), bar(100, 100.5, 99.6, 100), bar(100, 101, 99.7, 100.2)}
	if got := Episodes(grind, 100, 0.5); got != 1 {
		t.Fatalf("three bars inside the band are one visit, got %d", got)
	}
	twice := []market.Candle{bar(102, 102.5, 99.8, 100.2), bar(103, 104, 102, 103), bar(102, 102.2, 99.9, 101)}
	if got := Episodes(twice, 100, 0.5); got != 2 {
		t.Fatalf("leaving and returning is a second visit, got %d", got)
	}
	breakThrough := []market.Candle{bar(101.5, 102, 98, 98.5)}
	if got := Episodes(breakThrough, 100, 0.5); got != 0 {
		t.Fatalf("a bar that opens above and closes below the band broke it, it did not touch it: %d", got)
	}
	if Episodes(nil, 100, 1) != 0 || Episodes(grind, math.NaN(), 1) != 0 {
		t.Fatalf("no bars or no price is no episode")
	}
}

func TestClosesThroughCountsOnlyTheUndefendedSide(t *testing.T) {
	bars := []market.Candle{{Close: 99}, {Close: 98.9}, {Close: 101}, {Close: 97}, {Close: 101.2}}
	if got := ClosesThrough(bars, 100, 0.5, true); got != 2 {
		t.Fatalf("closes below 99 for a support: want 2, got %d", got)
	}
	if got := ClosesThrough(bars, 100, 0.5, false); got != 1 {
		t.Fatalf("closes above 101 for a resistance: want 1 (101 itself is not beyond 100+2*0.5), got %d", got)
	}
}

func TestOverallIsExactlyTheMeanOfTheDimensionsAndNothingElse(t *testing.T) {
	m := New("test.quality.v1")
	m.Score("a", 0.2)
	m.Score("b", 0.8)
	m.Score("c", 1.4) // clamped
	m.Score("d", math.NaN())
	m.Measure("width_atr", 7)     // a measurement never enters Overall
	m.Measure("lost", math.NaN()) // dropped, not published as a number
	if got, want := m.Overall(), (0.2+0.8+1+0)/4; math.Abs(got-want) > 1e-12 {
		t.Fatalf("Overall = %v, want %v", got, want)
	}
	c := m.Components()
	if _, present := c["lost"]; present {
		t.Fatalf("an unavailable measurement must be left out")
	}
	if c["score_c"] != 1 || c["score_d"] != 0 || c["width_atr"] != 7 {
		t.Fatalf("components = %v", c)
	}
	if err := Verify(m.Overall(), c); err != nil {
		t.Fatal(err)
	}
	if Verify(m.Overall()+0.01, c) == nil {
		t.Fatalf("Verify must catch an Overall that is not the mean")
	}
	c["score_a"] = 1.5
	if Verify(m.Overall(), c) == nil {
		t.Fatalf("Verify must catch a dimension outside [0, 1]")
	}
	if Verify(0.5, map[string]float64{"width_atr": 1}) == nil {
		t.Fatalf("a quality with no scored dimension is not a quality")
	}
}

func TestADimensionCannotBeScoredTwiceOrNamedLikeAMeasurement(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatalf("scoring a dimension twice must be a defect, not a silent overwrite")
		}
	}()
	m := New("x")
	m.Score("a", 1)
	m.Score("a", 1)
}

func TestMeasurementsCannotUseTheDimensionPrefix(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatalf("a measurement named score_* would enter Overall's namespace")
		}
	}()
	New("x").Measure("score_a", 1)
}

func TestSharedReactionHelpersAreDeterministicAndScaleFree(t *testing.T) {
	scale := func(k float64, bars []market.Candle) []market.Candle {
		out := make([]market.Candle, len(bars))
		for i, b := range bars {
			out[i] = market.Candle{Open: b.Open * k, High: b.High * k, Low: b.Low * k, Close: b.Close * k}
		}
		return out
	}
	bars := []market.Candle{{Open: 100, High: 100.4, Low: 99.2, Close: 99.9}, {Open: 99.9, High: 100.9, Low: 99.8, Close: 100.8}}
	for _, k := range []float64{1, 1000, 0.0001} {
		b := scale(k, bars)
		if got, want := RejectionScore(b, 0, 1, true), RejectionScore(bars, 0, 1, true); math.Abs(got-want) > 1e-9 {
			t.Fatalf("rejection depends on the price scale: %v vs %v", got, want)
		}
		if got, want := LocationScore(100.5*k, 100*k, 1*k), LocationScore(100.5, 100, 1); math.Abs(got-want) > 1e-9 {
			t.Fatalf("location depends on the price scale: %v vs %v", got, want)
		}
	}
	if RejectionScore(bars, 5, 1, true) != 0 || RejectionScore(bars, 0, -1, true) != 0 {
		t.Fatalf("indices outside the bars are missing evidence")
	}
}

func TestStructureScoreGradesContextWithoutAddingABonus(t *testing.T) {
	cases := []struct {
		buy         bool
		trend       string
		choch, htf  bool
		want        float64
		description string
	}{
		{true, "up", false, true, 1.0, "with structure and higher timeframe"},
		{true, "up", false, false, 0.7, "with structure only"},
		{true, "down", true, true, 0.9, "confirmed reversal with higher timeframe"},
		{true, "down", true, false, 0.7, "confirmed reversal alone"},
		{true, "range", false, true, 0.6, "range, aligned"},
		{true, "range", false, false, 0.5, "range"},
		{true, "down", false, true, 0.3, "against, no reversal evidence, aligned"},
		{true, "down", false, false, 0.1, "against with nothing"},
		{false, "down", false, true, 1.0, "sell mirrors buy"},
		{false, "up", true, false, 0.7, "sell reversal mirrors buy reversal"},
	}
	for _, c := range cases {
		if got := StructureScore(c.buy, c.trend, c.choch, c.htf); got != c.want {
			t.Errorf("%s: StructureScore = %v, want %v", c.description, got, c.want)
		}
	}
	// A confirmed reversal with no higher-timeframe help is not outranked by an unconfirmed counter-structure trade that has it.
	if StructureScore(true, "down", true, false) <= StructureScore(true, "down", false, true) {
		t.Fatalf("an HTF-aligned trade without reversal evidence must not outrank a confirmed reversal")
	}
}

func TestFirstVisitIsWhereALevelsLifeBegins(t *testing.T) {
	bar := func(o, h, l, c float64) market.Candle { return market.Candle{Open: o, High: h, Low: l, Close: c} }
	bars := []market.Candle{bar(95, 96, 94, 95), bar(96, 97, 95, 96), bar(99, 100.2, 98.8, 99.5), bar(100, 101, 99.7, 100.4)}
	if got := FirstVisit(bars, 100, 0.5); got != 2 {
		t.Fatalf("price first reached 99.5-100.5 on bar 2, got %d", got)
	}
	if FirstVisit(bars[:2], 100, 0.5) != -1 || FirstVisit(nil, 100, 0.5) != -1 || FirstVisit(bars, math.NaN(), 0.5) != -1 {
		t.Fatalf("no visit is -1")
	}
	// Closes below the level before it was ever visited are not failures of the level.
	if got := ClosesThrough(bars[FirstVisit(bars, 100, 0.5):], 100, 0.5, true); got != 0 {
		t.Fatalf("closes before the first visit must not count, got %d", got)
	}
}
