package flipzone

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// flip: a resistance at 102 that price broke on bar 10 (a strong candle), held above, and retested on bar 30 from above.
func flipFixture(trend string, htf bool, price float64) (techniquezone.Zone, *strategyutil.TechniqueDecision, strategyutil.QualityGeometry) {
	bars := make([]market.Candle, 40)
	for i := range bars {
		bars[i] = market.Candle{Open: 104, High: 104.5, Low: 103.6, Close: 104}
	}
	for i := 0; i < 26; i++ {
		bars[i] = market.Candle{Open: 100.5, High: 102, Low: 100, Close: 101}
	}
	bars[26] = market.Candle{Open: 101.2, High: 104, Low: 101.1, Close: 103.9} // the break, four bars before the retest
	bars[30] = market.Candle{Open: 103, High: 103.2, Low: 101.9, Close: 102.6} // the retest wick into the flipped zone
	bars[31] = market.Candle{Open: 102.6, High: 103.8, Low: 102.5, Close: 103.7}
	bias := ""
	if htf {
		bias = "up"
	}
	d := &strategyutil.LegacyDetector{
		Frame: &analysiscontext.LegacyFrame{Bars: bars, Structure: trend}, Read: analysiscontext.LegacyRead{HTFBias: bias},
		Direction: market.Buy, Price: price, ATR: 1, Settings: strategyutil.LegacyDetectorSettings{ReactionLookbackBars: 3},
	}
	dec := &strategyutil.TechniqueDecision{Technique: "flip_zone", Direction: market.Buy, Detector: d, Confirmation: &strategyutil.LegacyConfirmation{TouchIndex: 30, ConfirmationIndex: 31}}
	zone := techniquezone.Zone{Bottom: 101.8, Top: 102.3, Side: "demand", BreakIndex: 26}
	return zone, dec, strategyutil.QualityGeometry{Reference: 102.3, Invalidation: 101.3, Target: 105.3, Structural: true, ATR: 1}
}

func scoreFlip(t *testing.T, zone techniquezone.Zone, level techniquezone.Level, dec *strategyutil.TechniqueDecision, g strategyutil.QualityGeometry) (float64, map[string]float64) {
	t.Helper()
	q := scoreQuality(zone, level, dec, g)
	if err := quality.Verify(q.Overall, q.Components); err != nil {
		t.Fatal(err)
	}
	return q.Overall, q.Components
}

func TestStrongMediumAndWeakFlipsScoreDifferently(t *testing.T) {
	sz, sd, sg := flipFixture("up", true, 103.6)
	sg.Reference = 103.5
	s, _ := scoreFlip(t, sz, techniquezone.Level{Touches: 3}, sd, sg)

	mz, md, mg := flipFixture("range", false, 104)
	mg.Target = 104.6
	md.Detector.Frame.Bars[26] = market.Candle{Open: 101.2, High: 102.9, Low: 101.1, Close: 102.7} // a moderate break
	mz.BreakIndex = 22
	m, _ := scoreFlip(t, mz, techniquezone.Level{Touches: 2}, md, mg)

	wz, wd, wg := flipFixture("down", false, 105.3)
	wg.Structural = false
	wd.Detector.Frame.Bars[26] = market.Candle{Open: 102, High: 102.4, Low: 101.9, Close: 102.3} // barely through
	wd.Detector.Frame.Bars[30] = market.Candle{Open: 102.4, High: 102.5, Low: 102.2, Close: 102.3}
	wd.Detector.Frame.Bars[31] = market.Candle{Open: 102.3, High: 102.4, Low: 102.25, Close: 102.3}
	wz.BreakIndex = 0 // retested thirty bars after the break
	w, wc := scoreFlip(t, wz, techniquezone.Level{Touches: 9}, wd, wg)

	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.8 || m < 0.25 || m > 0.75 || w > 0.3 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestAFlipRetestedLongAfterTheBreakScoresBelowAQuickOne(t *testing.T) {
	timing := func(breakIndex int) float64 {
		z, d, g := flipFixture("up", true, 103.6)
		z.BreakIndex = breakIndex
		_, c := scoreFlip(t, z, techniquezone.Level{Touches: 3}, d, g)
		return c["score_retest_timing"]
	}
	// the retest touch is on bar 30; BreakIndex is the break bar
	if !(timing(29) == 1 && timing(24) == 1 && timing(10) < timing(24) && timing(-1) == 0) || !(timing(0) == 0) {
		t.Fatalf("retest timing: next bar %v, six bars %v, twenty bars %v, thirty bars %v", timing(29), timing(24), timing(10), timing(0))
	}
}
