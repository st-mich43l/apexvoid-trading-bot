package strategyutil

import (
	"math"
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// demandDecision is a confirmed reaction off a demand zone at 100-100.8 (ATR 1) that formed on bar 10 with a strong departure, was
// touched on bar 30 and confirmed on bar 31.
func demandDecision(touches int, trend string, htf bool) (*TechniqueDecision, QualityGeometry) {
	bars := make([]market.Candle, 40)
	for i := range bars {
		bars[i] = market.Candle{Open: 102, High: 102.5, Low: 101.6, Close: 102}
	}
	bars[10] = market.Candle{Open: 100.1, High: 100.8, Low: 100, Close: 100.7}
	bars[11] = market.Candle{Open: 100.7, High: 103.5, Low: 100.6, Close: 103.4} // the departure: 2.7 ATR above the zone
	bars[30] = market.Candle{Open: 101.2, High: 101.4, Low: 100.4, Close: 100.9} // a refused wick into the zone
	bars[31] = market.Candle{Open: 100.9, High: 101.9, Low: 100.85, Close: 101.8}
	htfBias := ""
	if htf {
		htfBias = "up"
	}
	d := &LegacyDetector{
		Frame: &analysiscontext.LegacyFrame{Bars: bars, Structure: trend}, Read: analysiscontext.LegacyRead{HTFBias: htfBias},
		Direction: market.Buy, Price: 101.8, ATR: 1, Settings: LegacyDetectorSettings{ReactionLookbackBars: 3},
	}
	dec := &TechniqueDecision{
		Technique: "supply_demand", Direction: market.Buy, Detector: d,
		Confirmation: &LegacyConfirmation{TouchIndex: 30, ConfirmationIndex: 31},
		Instance:     &techniquezone.Instance{Technique: "supply_demand", Side: "buy", Low: 100, High: 100.8, OriginIndex: 10, Touches: touches},
	}
	return dec, QualityGeometry{Reference: 100.8, Invalidation: 99.5, Target: 104.7, Structural: true, ATR: 1}
}

func zoneScore(t *testing.T, dec *TechniqueDecision, g QualityGeometry) (float64, map[string]float64) {
	t.Helper()
	q := ZoneFamilyQuality("supply", dec, g)
	if err := quality.Verify(q.Overall, q.Components); err != nil {
		t.Fatal(err)
	}
	return q.Overall, q.Components
}

func TestStrongMediumAndWeakZoneReactionsScoreDifferently(t *testing.T) {
	strong, sg := demandDecision(1, "up", true)
	sg.Reference = 101.3 // entry at the edge the price is at
	strong.Detector.Price = 101.4

	medium, mg := demandDecision(3, "range", false)
	medium.Detector.Price = 101.8
	mg.Target = 103.3

	weak, wg := demandDecision(6, "down", false)
	weak.Detector.Price = 102.9
	wg.Structural = false                                                                            // a fixed reward:risk target
	weak.Instance.Low, weak.Instance.High = 97, 101.5                                                // a very wide zone
	weak.Detector.Frame.Bars[11] = market.Candle{Open: 100.7, High: 100.9, Low: 100.6, Close: 100.8} // price left it timidly
	weak.Detector.Frame.Bars[31] = market.Candle{Open: 100.9, High: 101.0, Low: 100.85, Close: 100.95}

	s, _ := zoneScore(t, strong, sg)
	m, _ := zoneScore(t, medium, mg)
	w, wc := zoneScore(t, weak, wg)
	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.8 || m < 0.25 || m > 0.75 || w > 0.3 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestEveryRetestSpendsTheZone(t *testing.T) {
	prev := 2.0
	for touches := 1; touches <= 6; touches++ {
		dec, g := demandDecision(touches, "up", true)
		_, c := zoneScore(t, dec, g)
		if c["score_zone_freshness"] > prev {
			t.Fatalf("freshness rose with touches at %d", touches)
		}
		prev = c["score_zone_freshness"]
	}
	if prev != 0 {
		t.Fatalf("a zone retested five times has nothing left: %v", prev)
	}
}

func TestMissingEvidenceAndALackOfInstanceEarnNothing(t *testing.T) {
	dec, g := demandDecision(1, "up", true)
	dec.Instance = nil
	dec.Confirmation = nil
	g.Structural, g.ATR = false, 0
	_, c := zoneScore(t, dec, g)
	for _, name := range []string{"score_zone_departure", "score_zone_precision", "score_zone_freshness", "score_rejection", "score_entry_location", "score_room"} {
		if c[name] != 0 {
			t.Errorf("%s = %v without evidence", name, c[name])
		}
	}
}

func TestZoneScoreIgnoresBarsAfterTheReactionAndTheInstrumentScale(t *testing.T) {
	dec, g := demandDecision(1, "up", true)
	_, base := zoneScore(t, dec, g)
	later, g2 := demandDecision(1, "up", true)
	later.Detector.Frame.Bars = append(later.Detector.Frame.Bars, market.Candle{Open: 50, High: 200, Low: 1, Close: 150})
	_, after := zoneScore(t, later, g2)
	for name, v := range base {
		if after[name] != v {
			t.Fatalf("a later bar changed %s", name)
		}
	}
	for _, k := range []float64{1000, 0.0001} {
		s, sg := demandDecision(1, "up", true)
		for i, b := range s.Detector.Frame.Bars {
			s.Detector.Frame.Bars[i] = market.Candle{Open: b.Open * k, High: b.High * k, Low: b.Low * k, Close: b.Close * k}
		}
		s.Detector.Price, s.Detector.ATR = s.Detector.Price*k, s.Detector.ATR*k
		s.Instance.Low, s.Instance.High = s.Instance.Low*k, s.Instance.High*k
		sg.Reference, sg.Invalidation, sg.Target, sg.ATR = sg.Reference*k, sg.Invalidation*k, sg.Target*k, sg.ATR*k
		_, got := zoneScore(t, s, sg)
		for name, want := range base {
			if strings.HasPrefix(name, "score_") && math.Abs(got[name]-want) > 1e-9 {
				t.Errorf("scale %v: %s = %v, want %v", k, name, got[name], want)
			}
		}
	}
}
