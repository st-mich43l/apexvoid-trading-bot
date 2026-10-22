package trendline

import (
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	technical "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

func decision(trend string, htf bool, price float64) (*strategyutil.TechniqueDecision, strategyutil.QualityGeometry) {
	bars := make([]market.Candle, 40)
	for i := range bars {
		bars[i] = market.Candle{Open: 102, High: 102.5, Low: 101.6, Close: 102}
	}
	bars[30] = market.Candle{Open: 101.2, High: 101.4, Low: 100.4, Close: 100.9}
	bars[31] = market.Candle{Open: 100.9, High: 101.9, Low: 100.85, Close: 101.8}
	bias := ""
	if htf {
		bias = "up"
	}
	d := &strategyutil.LegacyDetector{
		Frame: &analysiscontext.LegacyFrame{Bars: bars, Structure: trend}, Read: analysiscontext.LegacyRead{HTFBias: bias},
		Direction: market.Buy, Price: price, ATR: 1, Settings: strategyutil.LegacyDetectorSettings{ReactionLookbackBars: 3},
	}
	dec := &strategyutil.TechniqueDecision{Technique: "trendline", Direction: market.Buy, Detector: d, Confirmation: &strategyutil.LegacyConfirmation{TouchIndex: 30, ConfirmationIndex: 31}}
	return dec, strategyutil.QualityGeometry{Reference: 101.3, Invalidation: 99.9, Target: 105.5, Structural: true, ATR: 1}
}

func line(validations, closeViolations, wickViolations, span int, slope float64) technical.Trendline {
	return technical.Trendline{ValidationTouches: make([]technical.ValidationTouch, validations), CloseViolations: closeViolations, WickViolations: wickViolations, SpanBars: span, SlopeATRPerBar: slope}
}

func scoreLine(t *testing.T, l technical.Trendline, approach float64, dec *strategyutil.TechniqueDecision, g strategyutil.QualityGeometry) (float64, map[string]float64) {
	t.Helper()
	q := scoreQuality(l, technical.Interaction{PreTouchDistanceATR: approach}, dec, g)
	if err := quality.Verify(q.Overall, q.Components); err != nil {
		t.Fatal(err)
	}
	return q.Overall, q.Components
}

func TestStrongMediumAndWeakTrendlinesScoreDifferently(t *testing.T) {
	sd, sg := decision("up", true, 101.4)
	sg.Reference = 101.3
	s, _ := scoreLine(t, line(3, 0, 0, 90, 0.04), 1.6, sd, sg)

	md, mg := decision("range", false, 101.8)
	mg.Target = 103.4
	m, _ := scoreLine(t, line(2, 0, 2, 45, 0.2), 0.8, md, mg)

	wd, wg := decision("down", false, 103)
	wg.Structural = false
	wd.Detector.Frame.Bars[31] = market.Candle{Open: 100.9, High: 101, Low: 100.85, Close: 100.95}
	wd.Detector.Frame.Bars[30] = market.Candle{Open: 101, High: 101.05, Low: 100.9, Close: 100.95}
	w, wc := scoreLine(t, line(0, 2, 3, 12, 0.5), 0.05, wd, wg)

	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.8 || m < 0.25 || m > 0.75 || w > 0.3 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestAWornLineScoresBelowARespectedOne(t *testing.T) {
	dec, g := decision("up", true, 101.4)
	val := func(n int) float64 {
		_, c := scoreLine(t, line(n, 0, 0, 90, 0.04), 1.6, dec, g)
		return c["score_line_validation"]
	}
	if !(val(0) == 0 && val(1) < val(2) && val(2) == val(3) && val(3) == val(4) && val(9) < val(4)) {
		t.Fatalf("validation touches must rise, plateau and fall: %v %v %v %v %v %v", val(0), val(1), val(2), val(3), val(4), val(9))
	}
	if _, c := scoreLine(t, line(3, 3, 0, 90, 0.04), 1.6, dec, g); c["score_line_integrity"] != 0 {
		t.Fatalf("three close violations (weighted 6 of 4) are ruin: %v", c["score_line_integrity"])
	}
	if _, c := scoreLine(t, line(3, 1, 0, 90, 0.04), 1.6, dec, g); c["score_line_integrity"] != 0.5 {
		t.Fatalf("one close violation (weighted 2 of 4) halves the integrity: %v", c["score_line_integrity"])
	}
}

func TestTrendlineScoreCarriesNoScoreTwiceAndNoUnmeasuredCredit(t *testing.T) {
	dec, g := decision("up", true, 101.4)
	_, c := scoreLine(t, line(3, 0, 0, 90, 0.04), 1.6, dec, g)
	n := 0
	for name := range c {
		if strings.HasPrefix(name, "score_") {
			n++
		}
	}
	if n != 8 {
		t.Fatalf("the trendline model has eight dimensions, got %d", n)
	}
	none := line(3, 0, 0, 0, 0.04) // span unknown
	dec.Confirmation = nil
	_, c = scoreLine(t, none, 0, dec, g)
	if c["score_rejection"] != 0 || c["score_approach"] != 0 {
		t.Fatalf("missing evidence scored: %v", c)
	}
}
