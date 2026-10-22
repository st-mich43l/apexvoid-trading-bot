package ifvg

import (
	"math"
	"reflect"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

func buySetup() Setup {
	return Setup{
		Direction: market.Buy,
		Gap:       Gap{WidthATR: 1.0, OriginBodyATR: 1.4},
		Inversion: Inversion{CloseBeyondATR: 0.5, DisplacementATR: 1.4, BarsFromGap: 6},
		Retest:    Retest{Touches: 1, BarsAfterInversion: 5, CloseStrength: 0.9, WickRatio: 0.55},
		EntryLow:  100, EntryHigh: 100.5, Stop: 99, Target: 103.5, ATR: 1, ZoneIntegrity: 1,
	}
}

func scoreOf(t *testing.T, setup Setup, qc qualityContext) (float64, map[string]float64) {
	t.Helper()
	m := quality.New(QualityModel)
	(&Strategy{}).score(m, setup, qc)
	if err := quality.Verify(m.Overall(), m.Components()); err != nil {
		t.Fatal(err)
	}
	return m.Overall(), m.Components()
}

var goodContext = qualityContext{price: 100.6, localTrend: "up", htfAligned: true}

func TestStrongMediumAndWeakInvertedGapsScoreDifferently(t *testing.T) {
	strong := buySetup()

	medium := buySetup()
	medium.Gap = Gap{WidthATR: 0.3, OriginBodyATR: 0.8}
	medium.Inversion = Inversion{CloseBeyondATR: 0.2, DisplacementATR: 0.6, BarsFromGap: 20}
	medium.Retest = Retest{Touches: 2, BarsAfterInversion: 15, CloseStrength: 0.7, WickRatio: 0.3}
	medium.ZoneIntegrity = 0.7
	medium.Target = 102.5

	weak := buySetup()
	weak.Gap = Gap{WidthATR: 0.05, OriginBodyATR: 0.2}                                           // a sliver left by a small candle
	weak.Inversion = Inversion{CloseBeyondATR: 0.02, DisplacementATR: 4.5, BarsFromGap: 1}       // a spike in the same move
	weak.Retest = Retest{Touches: 4, BarsAfterInversion: 60, CloseStrength: 0.5, WickRatio: 0.1} // worn and late
	weak.ZoneIntegrity = 0.2
	weak.Target = 100.9 // no room

	s, _ := scoreOf(t, strong, goodContext)
	m, _ := scoreOf(t, medium, qualityContext{price: 101.2, localTrend: "range"})
	w, wc := scoreOf(t, weak, qualityContext{price: 101.8, localTrend: "down"})
	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.85 || m < 0.25 || m > 0.65 || w > 0.15 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f are outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestTheInversionCandleIsCreditedInOneDimensionOnly(t *testing.T) {
	base := buySetup()
	_, before := scoreOf(t, base, goodContext)
	varied := base
	varied.Inversion.DisplacementATR, varied.Inversion.CloseBeyondATR = 0.4, 0.06
	_, after := scoreOf(t, varied, goodContext)
	for name, v := range before {
		if len(name) < 6 || name[:6] != "score_" || name == "score_inversion_strength" {
			continue
		}
		if math.Abs(after[name]-v) > 1e-12 {
			t.Errorf("changing the inversion candle moved %s: %v -> %v", name, v, after[name])
		}
	}
	if !(after["score_inversion_strength"] < before["score_inversion_strength"]) {
		t.Fatalf("a weaker inversion must score lower")
	}
}

func TestAnExhaustedInversionSpikeScoresBelowADecisiveOne(t *testing.T) {
	decisive, spike := buySetup(), buySetup()
	spike.Inversion.DisplacementATR = 5
	_, d := scoreOf(t, decisive, goodContext)
	_, s := scoreOf(t, spike, goodContext)
	if !(s["score_inversion_strength"] < d["score_inversion_strength"]) {
		t.Fatalf("bigger is not better: spike %v decisive %v", s["score_inversion_strength"], d["score_inversion_strength"])
	}
}

func TestMissingEvidenceEarnsNothingAndNoFutureBarIsRead(t *testing.T) {
	setup := buySetup()
	setup.ATR = 0                  // no volatility: no location, no way to measure
	setup.Target = setup.EntryHigh // no room
	_, c := scoreOf(t, setup, qualityContext{price: math.NaN()})
	if c["score_entry_location"] != 0 || c["score_room"] != 0 {
		t.Fatalf("missing evidence scored: %v", c)
	}
	// The score is a function of the setup and the latest close only; it takes no series, so no later candle can reach it.
	a, ac := scoreOf(t, buySetup(), goodContext)
	b, bc := scoreOf(t, buySetup(), goodContext)
	if a != b || !reflect.DeepEqual(ac, bc) {
		t.Fatal("not reproducible")
	}
}

func TestTheSameGeometryScoresTheSameOnAnyPriceScale(t *testing.T) {
	_, base := scoreOf(t, buySetup(), goodContext)
	for _, k := range []float64{1000, 0.0001} {
		s := buySetup()
		s.EntryLow, s.EntryHigh, s.Stop, s.Target, s.ATR = s.EntryLow*k, s.EntryHigh*k, s.Stop*k, s.Target*k, s.ATR*k
		qc := goodContext
		qc.price *= k
		_, got := scoreOf(t, s, qc)
		for name, want := range base {
			if len(name) > 6 && name[:6] == "score_" && math.Abs(got[name]-want) > 1e-9 {
				t.Errorf("scale %v: %s = %v, want %v", k, name, got[name], want)
			}
		}
	}
}
