package keylevel

import (
	"math"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

// supportHistory builds closed bars around a support at 100 (ATR 1, band 0.1): each episode is a bar that visits the level and is
// followed by bars well above it. The last two bars are the reaction: a touch bar with the given lower wick and a confirmation bar
// closing at closeAt of its range. It returns the bars and the touch and confirmation indices.
func supportHistory(episodes int, wick, closeAt float64) ([]market.Candle, int, int) {
	var bars []market.Candle
	for i := 0; i < episodes; i++ {
		bars = append(bars,
			market.Candle{Open: 102, High: 102.4, Low: 101.6, Close: 102.1},
			market.Candle{Open: 101.2, High: 101.4, Low: 99.95, Close: 101},
			market.Candle{Open: 101, High: 102.2, Low: 100.9, Close: 102},
			market.Candle{Open: 102, High: 102.5, Low: 101.5, Close: 102.2},
			market.Candle{Open: 102.2, High: 102.6, Low: 101.7, Close: 102},
		)
	}
	touch := len(bars)
	// The touch bar: range 1.0 from 99.9 to 100.9, so the lower wick fraction is wick.
	low, high := 99.9, 100.9
	bodyLow := low + wick*(high-low)
	bars = append(bars, market.Candle{Open: bodyLow + 0.2, High: high, Low: low, Close: bodyLow + 0.1})
	confirm := len(bars)
	bars = append(bars, market.Candle{Open: 100.4, High: 100.4 + 1.0, Low: 100.4, Close: 100.4 + closeAt})
	return bars, touch, confirm
}

func baseInputs(episodes int, wick, closeAt float64) qualityInputs {
	bars, touch, confirm := supportHistory(episodes, wick, closeAt)
	return qualityInputs{
		bars: bars, touchIndex: touch, confirmIndex: confirm, level: 100, band: 0.1, atr: 1, buy: true,
		price: 100.4 + closeAt, reference: 100.9, invalidation: 99.4, target: 103.4, structural: true,
		localTrend: "up", htfAligned: true, htfTouches: 3, htfWeight: 1,
	}
}

func score(t *testing.T, in qualityInputs) (float64, map[string]float64) {
	t.Helper()
	q := scoreQuality(in)
	if err := quality.Verify(q.Overall, q.Components); err != nil {
		t.Fatal(err)
	}
	return q.Overall, q.Components
}

func TestStrongMediumAndWeakKeyLevelsScoreDifferently(t *testing.T) {
	strong := baseInputs(3, 0.6, 0.95)
	strong.price = 100.95

	medium := baseInputs(2, 0.3, 0.7)
	medium.htfTouches, medium.htfWeight = 0, 0 // a plain M5 level
	medium.price, medium.localTrend = 101.5, "range"
	medium.target = 102.9

	weak := baseInputs(1, 0.05, 0.5)
	weak.htfTouches, weak.htfWeight = 0, 0
	weak.localTrend, weak.htfAligned = "down", false // against the market, no reversal evidence
	weak.structural = false                          // a fixed reward:risk target: no structural room
	weak.price = 102.3
	// the level keeps closing through
	weak.bars = append([]market.Candle{{Open: 99, High: 99.1, Low: 97.5, Close: 98}, {Open: 98, High: 98.2, Low: 97, Close: 97.4}, {Open: 97.4, High: 98.9, Low: 97.2, Close: 98.5}}, weak.bars...)
	weak.touchIndex, weak.confirmIndex = weak.touchIndex+3, weak.confirmIndex+3

	s, sc := score(t, strong)
	m, mc := score(t, medium)
	w, wc := score(t, weak)
	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f and weak %.3f must be ordered", s, m, w)
	}
	if s < 0.85 || m < 0.3 || m > 0.7 || w > 0.3 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f are not in the bands the rubric documents: %v / %v / %v", s, m, w, sc, mc, wc)
	}
}

func TestMoreTouchesStopHelpingAndThenHurt(t *testing.T) {
	// priorEpisodes visits before the reaction; the reaction itself is one more episode.
	significance := func(prior int) float64 {
		_, c := score(t, baseInputs(prior, 0.6, 0.95))
		return c["score_level_significance"]
	}
	if !(significance(0) < significance(1) && significance(1) < significance(2)) {
		t.Fatalf("a level needs a few independent tests to be one: %v %v %v", significance(0), significance(1), significance(2))
	}
	if significance(2) != 1 || significance(6) != 1 || significance(11) != 1 {
		t.Fatalf("three to twelve tests are a plateau, not a ladder: %v %v %v", significance(2), significance(6), significance(11))
	}
	if !(significance(20) < significance(11) && significance(40) == 0) {
		t.Fatalf("a level tested over and over has been worn down: %v vs %v, %v", significance(20), significance(11), significance(40))
	}
}

func TestACandleThatStaysInsideTheBandIsOneEpisodeNotTwo(t *testing.T) {
	in := baseInputs(2, 0.6, 0.95)
	_, before := score(t, in)
	// Duplicate the visit bar: the same visit lasted two bars.
	dup := append([]market.Candle(nil), in.bars[:2]...)
	dup = append(dup, in.bars[1])
	dup = append(dup, in.bars[2:]...)
	in.bars, in.touchIndex, in.confirmIndex = dup, in.touchIndex+1, in.confirmIndex+1
	_, after := score(t, in)
	if before["level_episodes"] != after["level_episodes"] {
		t.Fatalf("repeating the same visit inflated the evidence: %v -> %v", before["level_episodes"], after["level_episodes"])
	}
}

func TestAHigherTimeframeAlignedTradeDoesNotOutrankAStrongConfirmedReversal(t *testing.T) {
	aligned := baseInputs(1, 0.1, 0.55) // weak reaction, marginal level
	aligned.htfTouches, aligned.htfWeight = 0, 0
	aligned.price = 102
	reversal := baseInputs(3, 0.6, 0.95) // a respected level, a clean reaction
	reversal.localTrend, reversal.htfAligned, reversal.choch = "down", false, true
	reversal.price = 100.95
	a, _ := score(t, aligned)
	r, _ := score(t, reversal)
	if !(r > a) {
		t.Fatalf("four generic points must not decide: reversal %.3f, weak aligned %.3f", r, a)
	}
}

func TestMissingEvidenceNeverEarnsCredit(t *testing.T) {
	in := baseInputs(3, 0.6, 0.95)
	in.htfTouches, in.htfWeight = 0, 0
	in.structural = false
	in.touchIndex, in.confirmIndex = -1, len(in.bars)+5 // the reaction candles are not available
	in.atr = 0
	_, c := score(t, in)
	for _, name := range []string{"score_htf_level", "score_room", "score_rejection", "score_entry_location"} {
		if c[name] != 0 {
			t.Errorf("%s = %v with no evidence", name, c[name])
		}
	}
	if _, present := c["entry_chase_atr"]; present {
		t.Errorf("a measurement that cannot be taken must be left out, not published")
	}
}

func TestNoFutureCandleChangesTheScore(t *testing.T) {
	in := baseInputs(3, 0.6, 0.95)
	_, before := score(t, in)
	future := in
	future.bars = append(append([]market.Candle(nil), in.bars...),
		market.Candle{Open: 95, High: 96, Low: 90, Close: 91}, market.Candle{Open: 91, High: 99.9, Low: 85, Close: 99.95})
	_, after := score(t, future)
	if !reflect.DeepEqual(before, after) {
		t.Fatalf("bars after the confirmation changed the published score:\n%v\n%v", before, after)
	}
}

func TestTheSameStructureScoresTheSameOnAnyPriceScale(t *testing.T) {
	in := baseInputs(3, 0.6, 0.95)
	_, base := score(t, in)
	for _, k := range []float64{1000, 0.0001} {
		scaled := in
		scaled.bars = make([]market.Candle, len(in.bars))
		for i, b := range in.bars {
			scaled.bars[i] = market.Candle{Open: b.Open * k, High: b.High * k, Low: b.Low * k, Close: b.Close * k}
		}
		scaled.level, scaled.band, scaled.atr = in.level*k, in.band*k, in.atr*k
		scaled.price, scaled.reference, scaled.invalidation, scaled.target = in.price*k, in.reference*k, in.invalidation*k, in.target*k
		_, got := score(t, scaled)
		for name, want := range base {
			if !math.IsNaN(want) && !(len(name) > 5 && name[:6] == "score_") {
				continue // raw measurements carry their own units
			}
			if math.Abs(got[name]-want) > 1e-9 {
				t.Errorf("scale %v: %s = %v, want %v", k, name, got[name], want)
			}
		}
	}
}

func TestScoresAreReproducibleFiniteAndBounded(t *testing.T) {
	in := baseInputs(3, 0.6, 0.95)
	a, ac := score(t, in)
	b, bc := score(t, in)
	if a != b || !reflect.DeepEqual(ac, bc) {
		t.Fatal("the same inputs gave different scores")
	}
	for name, v := range ac {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			t.Errorf("%s = %v", name, v)
		}
		if len(name) > 6 && name[:6] == "score_" && (v < 0 || v > 1) {
			t.Errorf("%s = %v outside [0, 1]", name, v)
		}
	}
	if a < 0 || a > 1 {
		t.Fatalf("overall %v", a)
	}
}

func TestIntegrityIsTheShareOfFailedClosesSinceTheFirstVisit(t *testing.T) {
	in := baseInputs(3, 0.6, 0.95)
	_, clean := score(t, in)
	if clean["score_level_integrity"] != 1 {
		t.Fatalf("a level that never failed a close: %v", clean["score_level_integrity"])
	}
	// Insert closes below the level after its first visit: a level that keeps failing.
	broken := in
	bars := append([]market.Candle(nil), in.bars[:6]...)
	for i := 0; i < 8; i++ {
		bars = append(bars, market.Candle{Open: 98.8, High: 99, Low: 98.2, Close: 98.5})
	}
	bars = append(bars, in.bars[6:]...)
	broken.bars, broken.touchIndex, broken.confirmIndex = bars, in.touchIndex+8, in.confirmIndex+8
	_, c := score(t, broken)
	if !(c["score_level_integrity"] < 0.5) {
		t.Fatalf("eight failed closes in %d bars should wreck the integrity: %v", len(bars), c["score_level_integrity"])
	}
	// What happened before the level was ever visited does not count.
	early := in
	history := []market.Candle{}
	for i := 0; i < 30; i++ {
		history = append(history, market.Candle{Open: 90, High: 90.5, Low: 89.5, Close: 90})
	}
	early.bars = append(history, in.bars...)
	early.touchIndex, early.confirmIndex = in.touchIndex+30, in.confirmIndex+30
	if _, c := score(t, early); c["score_level_integrity"] != 1 {
		t.Fatalf("closes below the level before its first visit were counted: %v", c["score_level_integrity"])
	}
	// Too little history: unmeasurable, no credit.
	fresh := baseInputs(0, 0.6, 0.95)
	if _, c := score(t, fresh); c["score_level_integrity"] != 0 {
		t.Fatalf("a level with no history earned integrity: %v", c["score_level_integrity"])
	}
}
