package techniquezone

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// flatThenGap is twelve quiet bars near 100 and then a fair value gap: bars[12] is the first candle (high 100.5), bars[13] the displacement candle and
// bars[14] the third candle (low 101.5), so the bullish gap is 100.5-101.5 (1.0 wide) with its origin at 14. The caller shapes the middle candle.
func flatThenGap(middle market.Candle) []market.Candle {
	bars := make([]market.Candle, 0, 18)
	for i := 0; i < 12; i++ {
		bars = append(bars, bar(i, 100, 100.4, 99.6, 100))
	}
	bars = append(bars, bar(12, 100.2, 100.5, 99.9, 100.3))
	middle.Time = int64(13) * 300
	bars = append(bars, middle)
	bars = append(bars, bar(14, 102.0, 102.8, 101.5, 102.6))
	// the gap keeps living for a few bars above it
	for i := 15; i < 18; i++ {
		bars = append(bars, bar(i, 102.4, 102.9, 101.9, 102.5))
	}
	return bars
}

var (
	// a real displacement: body 2.2, range 2.5, closed up
	strongUp = market.Candle{Open: 100.4, High: 102.8, Low: 100.3, Close: 102.6}
	gapUp    = Instance{Technique: "fvg", Side: "buy", Low: 100.5, High: 101.5, OriginIndex: 14}
)

func constantATR(n int, v float64) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}

func definition(gap, bodyATR, ratio float64) TechniqueSettings {
	s := ProductionTechniqueSettings()
	s.FVGMinGapATR, s.FVGMinDisplacementBodyATR, s.FVGMinDisplacementBodyRatio = gap, bodyATR, ratio
	return s
}

func TestAGapThatMeetsTheDefinitionPasses(t *testing.T) {
	bars := flatThenGap(strongUp)
	// ATR 1.5: the gap (1.0) is 0.67 ATR, the body (2.2) 1.47 ATR and 88% of its range
	if got := fvgDefinitionRefusal(gapUp, bars, 1.5, constantATR(len(bars), 1.5), definition(.5, .5, .5)); got != "" {
		t.Fatalf("a real gap was refused: %q", got)
	}
}

func TestEachConditionRefusesOnItsOwnAndIsSkippedWhenItsSettingIsZero(t *testing.T) {
	bars := flatThenGap(strongUp)
	series := constantATR(len(bars), 3.0) // gap 1.0 = 0.33 ATR; body 2.2 = 0.73 ATR
	if got := fvgDefinitionRefusal(gapUp, bars, 3.0, series, definition(.5, 0, 0)); got != FVGRefusedGapSmall {
		t.Fatalf("gap 0.33 ATR against 0.5: %q", got)
	}
	if got := fvgDefinitionRefusal(gapUp, bars, 3.0, series, definition(0, .8, 0)); got != FVGRefusedDisplacementWeak {
		t.Fatalf("body 0.73 ATR against 0.8: %q", got)
	}
	// a body that is a small share of its own range (long wicks), closed up
	wicky := market.Candle{Open: 100.4, High: 105.0, Low: 98.0, Close: 102.6}
	if got := fvgDefinitionRefusal(gapUp, flatThenGap(wicky), 1.5, constantATR(len(bars), 1.5), definition(0, 0, .5)); got != FVGRefusedDisplacementWeak {
		t.Fatalf("body 2.2 of range 7.0 (31%%) against 50%%: %q", got)
	}
	// the displacement candle closed AGAINST the gap
	against := market.Candle{Open: 102.6, High: 102.8, Low: 100.3, Close: 100.4}
	if got := fvgDefinitionRefusal(gapUp, flatThenGap(against), 1.5, constantATR(len(bars), 1.5), definition(0, .5, 0)); got != FVGRefusedDisplacementDirection {
		t.Fatalf("a bearish middle candle under a bullish gap: %q", got)
	}
	// every condition off: nothing to judge
	if got := fvgDefinitionRefusal(gapUp, flatThenGap(against), 3.0, series, definition(0, 0, 0)); got != "" {
		t.Fatalf("with no setting on the definition must not refuse: %q", got)
	}
	// direction belongs to the displacement settings: a gap-only definition does not look at it
	if got := fvgDefinitionRefusal(gapUp, flatThenGap(against), 1.5, constantATR(len(bars), 1.5), definition(.5, 0, 0)); got != "" {
		t.Fatalf("a gap-only definition must not judge the candle's direction: %q", got)
	}
}

func TestABearishGapNeedsABearishDisplacementCandle(t *testing.T) {
	bars := flatThenGap(strongUp)
	sellGap := Instance{Technique: "fvg", Side: "sell", Low: 100.5, High: 101.5, OriginIndex: 14}
	if got := fvgDefinitionRefusal(sellGap, bars, 1.5, constantATR(len(bars), 1.5), definition(.5, .5, .5)); got != FVGRefusedDisplacementDirection {
		t.Fatalf("a bullish middle candle under a bearish gap: %q", got)
	}
	bearish := market.Candle{Open: 102.6, High: 102.8, Low: 100.3, Close: 100.4}
	if got := fvgDefinitionRefusal(sellGap, flatThenGap(bearish), 1.5, constantATR(len(bars), 1.5), definition(.5, .5, .5)); got != "" {
		t.Fatalf("a bearish middle candle under a bearish gap passes: %q", got)
	}
}

func TestAClippedGapIsJudgedOnItsStructuralWidth(t *testing.T) {
	bars := flatThenGap(strongUp)
	// the entry was clipped to 0.4 but the structure is the full 1.0 gap
	clipped := Instance{Technique: "fvg", Side: "buy", Low: 101.1, High: 101.5, OriginIndex: 14, EntryClipped: true, StructuralLow: 100.5, StructuralHigh: 101.5}
	if got := fvgDefinitionRefusal(clipped, bars, 1.5, constantATR(len(bars), 1.5), definition(.5, .5, .5)); got != "" {
		t.Fatalf("the structural gap is 0.67 ATR, the clipped 0.27: it must be judged on the structure, got %q", got)
	}
	notClipped := clipped
	notClipped.EntryClipped = false
	if got := fvgDefinitionRefusal(notClipped, bars, 1.5, constantATR(len(bars), 1.5), definition(.5, .5, .5)); got != FVGRefusedGapSmall {
		t.Fatalf("the same band unclipped is 0.27 ATR: %q", got)
	}
}

func TestTheATRIsTheFramesAsOfTheThirdCandleAndNothingLaterMatters(t *testing.T) {
	bars := flatThenGap(strongUp)
	s := definition(.5, .5, .5)
	series := constantATR(len(bars), 3.0)
	series[14] = 1.5 // the ATR when the gap formed; it is 3.0 on every bar after
	if got := fvgDefinitionRefusal(gapUp, bars, 3.0, series, s); got != "" {
		t.Fatalf("judged on the ATR of the third candle (1.5) the gap passes: %q", got)
	}
	// the scalar the caller judged the frame with (the median, 3.0) is only the fallback
	if got := fvgDefinitionRefusal(gapUp, bars, 3.0, nil, s); got != FVGRefusedGapSmall {
		t.Fatalf("without the series the scalar is used: %q", got)
	}
	if got := fvgDefinitionRefusal(gapUp, bars, 3.0, series[:len(series)-1], s); got != FVGRefusedGapSmall {
		t.Fatalf("a series that is not aligned with the bars is not used: %q", got)
	}
	// causal: what happens after the third candle changes nothing
	later := append([]market.Candle(nil), bars...)
	for i := 15; i < len(later); i++ {
		later[i] = bar(i, 130, 140, 120, 135)
	}
	laterSeries := append([]float64(nil), series...)
	for i := 15; i < len(laterSeries); i++ {
		laterSeries[i] = 40
	}
	if a, b := fvgDefinitionRefusal(gapUp, bars, 3.0, series, s), fvgDefinitionRefusal(gapUp, later, 3.0, laterSeries, s); a != b {
		t.Fatalf("a candle after the gap formed changed the verdict: %q vs %q", a, b)
	}
}

func TestACollectedGapIsOfferedOnlyWhenItMeetsTheDefinitionAndTheRefusalIsReported(t *testing.T) {
	bars := flatThenGap(strongUp)
	zone := newZone(100.5, 101.5, "demand", 14, "bullish_fvg")
	var refused []string
	collect := func(s TechniqueSettings, atr float64) []Instance {
		refused = nil
		s.IgnoreProximity = true
		return CollectInstancesWithATR(nil, nil, []Zone{zone}, bars, bars[len(bars)-1].Close, atr, constantATR(len(bars), atr), s, func(in Instance, reason string) {
			if in.Technique != "fvg" {
				t.Fatalf("only fair value gaps are refused by this definition: %+v", in)
			}
			refused = append(refused, reason)
		})
	}
	if got := collect(definition(0, 0, 0), 3.0); len(got) != 1 || len(refused) != 0 {
		t.Fatalf("with the definition off the 1.0 gap is offered as before: %d offered, refused %v", len(got), refused)
	}
	if got := collect(definition(.5, .5, .5), 1.5); len(got) != 1 || len(refused) != 0 {
		t.Fatalf("a gap that meets the definition is offered: %d, refused %v", len(got), refused)
	}
	if got := collect(definition(.5, .5, .5), 3.0); len(got) != 0 || len(refused) != 1 || refused[0] != FVGRefusedGapSmall {
		t.Fatalf("a thin gap is refused and reported: %d offered, refused %v", len(got), refused)
	}
}

func TestAGapThatFailsAnotherTestIsNotCountedAsRefusedByTheDefinition(t *testing.T) {
	// a gap wider than FVGMaxATR of the frame's ATR is the width test's refusal (it is judged before the definition): the definition reports only gaps
	// every other test would have offered
	bars := flatThenGap(strongUp)
	zone := newZone(100.5, 101.5, "demand", 14, "bullish_fvg")
	s := definition(.5, .5, .5)
	s.IgnoreProximity = true
	var refused []string
	got := CollectInstancesWithATR(nil, nil, []Zone{zone}, bars, bars[len(bars)-1].Close, 0.4, constantATR(len(bars), 0.4), s, func(_ Instance, reason string) { refused = append(refused, reason) })
	if len(got) != 0 || len(refused) != 0 {
		t.Fatalf("a gap wider than %.1f ATR is not the definition's refusal: offered %d refused %v", s.FVGMaxATR, len(got), refused)
	}
}
