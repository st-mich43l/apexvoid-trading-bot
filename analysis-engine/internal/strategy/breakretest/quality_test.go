package breakretest

import (
	"math"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

func goodBreak() Setup {
	return Setup{
		Direction: market.Buy,
		Reference: Reference{Touches: 3},
		Break:     Break{DisplacementATR: 1.3, DistanceATR: 0.6, AcceptCloses: 2},
		Retest:    Retest{DepthATR: 0.25, BarsAfterAcceptance: 6, CloseStrength: 0.9, WickRatio: 0.5},
		EntryLow:  100, EntryHigh: 100.5, Stop: 99, Target: 103.8, ATR: 1,
	}
}

func scoreBreak(t *testing.T, s Setup, qc qualityContext) (float64, map[string]float64) {
	t.Helper()
	m := quality.New(QualityModel)
	(&Strategy{}).score(m, s, qc)
	if err := quality.Verify(m.Overall(), m.Components()); err != nil {
		t.Fatal(err)
	}
	return m.Overall(), m.Components()
}

var withTrend = qualityContext{price: 100.6, localTrend: "up", htfAligned: true}

func TestStrongMediumAndWeakBreakRetestsScoreDifferently(t *testing.T) {
	strong := goodBreak()

	medium := goodBreak()
	medium.Reference.Touches = 2
	medium.Break = Break{DisplacementATR: 0.7, DistanceATR: 0.25, AcceptCloses: 2}
	medium.Retest = Retest{DepthATR: 0.5, BarsAfterAcceptance: 20, CloseStrength: 0.7, WickRatio: 0.3}
	medium.Target = 102.6

	weak := goodBreak()
	weak.Reference.Touches = 1
	weak.Break = Break{DisplacementATR: 0.45, DistanceATR: 0.1, AcceptCloses: 2}
	weak.Retest = Retest{DepthATR: 1.1, BarsAfterAcceptance: 38, CloseStrength: 0.5, WickRatio: 0.1} // cut back through the level, very late
	weak.Target = 100.7

	s, _ := scoreBreak(t, strong, withTrend)
	m, _ := scoreBreak(t, medium, qualityContext{price: 101.3, localTrend: "range"})
	w, wc := scoreBreak(t, weak, qualityContext{price: 102, localTrend: "down"})
	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.8 || m < 0.25 || m > 0.75 || w > 0.3 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestTheBreakCandleIsScoredOnceAndAnExhaustedBreakScoresBelowADecisiveOne(t *testing.T) {
	_, base := scoreBreak(t, goodBreak(), withTrend)
	spike := goodBreak()
	spike.Break.DisplacementATR = 6
	_, after := scoreBreak(t, spike, withTrend)
	for name, v := range base {
		if strings.HasPrefix(name, "score_") && name != "score_break_force" && math.Abs(after[name]-v) > 1e-12 {
			t.Errorf("the break candle moved %s", name)
		}
	}
	if !(after["score_break_force"] < base["score_break_force"]) {
		t.Fatalf("a spike must not outscore a decisive break")
	}
	if _, present := base["score_acceptance"]; present {
		t.Fatalf("acceptance is a validity rule with one value, not a scored dimension")
	}
}

func TestBreakRetestMissingEvidenceEarnsNothing(t *testing.T) {
	s := goodBreak()
	s.ATR = 0
	s.Target = s.EntryHigh
	_, c := scoreBreak(t, s, qualityContext{price: math.NaN()})
	if c["score_entry_location"] != 0 || c["score_room"] != 0 {
		t.Fatalf("missing evidence scored: %v", c)
	}
}
