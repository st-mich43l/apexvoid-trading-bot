package fadescalp

import (
	"math"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// buyFade is a buy fade of a swept equal-low pool at 100 (ATR 1): a refused wick through the pool on bar 30, then a close back
// above it on bar 31.
func buyFade() qualityInputs {
	bars := make([]market.Candle, 40)
	for i := range bars {
		bars[i] = market.Candle{Open: 101, High: 101.4, Low: 100.6, Close: 101}
	}
	bars[30] = market.Candle{Open: 100.2, High: 100.5, Low: 99.6, Close: 100.4}   // the sweep: wick below the pool, refused
	bars[31] = market.Candle{Open: 100.4, High: 101.4, Low: 100.35, Close: 101.3} // the confirmation: closes back through the pool
	return qualityInputs{
		bars: bars, direction: market.Buy,
		pool: techniquezone.Pool{Side: "sell", Level: 100, Band: 0.1, Touches: 3, LastIndex: 10},
		grab: &techniquezone.Grab{Index: 30, Grade: "A"}, confirmation: &strategyutil.LegacyConfirmation{TouchIndex: 30, ConfirmationIndex: 31},
		facts: map[string]float64{"penetration_atr": 0.35, "run_into_sweep_atr": 2.5},
		atr:   1, price: 101.3, reference: 101.2, invalidation: 99.4, objective: 105,
	}
}

func scoreFade(t *testing.T, in qualityInputs) (float64, map[string]float64) {
	t.Helper()
	m := scoreQuality(in)
	if err := quality.Verify(m.Overall(), m.Components()); err != nil {
		t.Fatal(err)
	}
	return m.Overall(), m.Components()
}

func TestStrongMediumAndWeakFadesScoreDifferently(t *testing.T) {
	strong := buyFade()

	medium := buyFade()
	medium.grab = &techniquezone.Grab{Index: 30, Grade: "B"}
	medium.pool.Touches, medium.pool.Band = 2, 0.3
	medium.facts = map[string]float64{"penetration_atr": 0.15, "run_into_sweep_atr": 1.0}
	medium.price, medium.objective = 101.8, 103.6

	weak := buyFade()
	weak.grab = &techniquezone.Grab{Index: 30, Grade: "B"}
	weak.pool.Touches, weak.pool.Band, weak.pool.LastIndex = 2, 0.55, 38
	weak.facts = map[string]float64{"penetration_atr": 1.4, "run_into_sweep_atr": 0.2}
	weak.bars = append([]market.Candle(nil), weak.bars...)
	weak.bars[31] = market.Candle{Open: 100.4, High: 100.6, Low: 100.3, Close: 100.45} // a weak, indecisive confirmation
	weak.price, weak.objective = 102.7, 101.6

	s, _ := scoreFade(t, strong)
	m, _ := scoreFade(t, medium)
	w, wc := scoreFade(t, weak)
	if !(s > m && m > w) {
		t.Fatalf("strong %.3f, medium %.3f, weak %.3f must be ordered", s, m, w)
	}
	if s < 0.8 || m < 0.25 || m > 0.75 || w > 0.3 {
		t.Fatalf("scores strong=%.3f medium=%.3f weak=%.3f outside the documented bands (weak: %v)", s, m, w, wc)
	}
}

func TestFadeQualityHasNoTrendAlignmentDimensionAndExhaustionNeedsSomethingToExhaust(t *testing.T) {
	m := scoreQuality(buyFade())
	for _, name := range m.Dimensions() {
		if strings.Contains(name, "structure") || strings.Contains(name, "trend") || strings.Contains(name, "htf") {
			t.Fatalf("a fade is not scored by trend alignment: %s", name)
		}
	}
	quiet, stretched, spike := buyFade(), buyFade(), buyFade()
	quiet.facts = map[string]float64{"penetration_atr": 0.35, "run_into_sweep_atr": 0.1}
	spike.facts = map[string]float64{"penetration_atr": 0.35, "run_into_sweep_atr": 12}
	_, q := scoreFade(t, quiet)
	_, s := scoreFade(t, stretched)
	_, p := scoreFade(t, spike)
	if !(s["score_extension"] > q["score_extension"] && s["score_extension"] > p["score_extension"]) {
		t.Fatalf("exhaustion needs a stretched move, and a spike is a trend: quiet %v stretched %v spike %v", q["score_extension"], s["score_extension"], p["score_extension"])
	}
}

func TestADeepPenetrationIsABreakoutNotAStrongerRaid(t *testing.T) {
	shallow, deep := buyFade(), buyFade()
	deep.facts = map[string]float64{"penetration_atr": 3, "run_into_sweep_atr": 2.5}
	_, a := scoreFade(t, shallow)
	_, b := scoreFade(t, deep)
	if !(b["score_sweep"] < a["score_sweep"]) {
		t.Fatalf("deep %v shallow %v", b["score_sweep"], a["score_sweep"])
	}
}

func TestALateEntryAfterABigDisplacementScoresLowerAndMissingEvidenceNothing(t *testing.T) {
	early, late := buyFade(), buyFade()
	late.price = 103
	_, e := scoreFade(t, early)
	_, l := scoreFade(t, late)
	if !(l["score_entry_location"] < e["score_entry_location"]) {
		t.Fatalf("late %v early %v", l["score_entry_location"], e["score_entry_location"])
	}
	none := buyFade()
	none.facts = map[string]float64{}
	none.atr = 0
	_, c := scoreFade(t, none)
	if c["score_extension"] != 0 || c["score_entry_location"] != 0 {
		t.Fatalf("missing evidence scored: %v", c)
	}
}

func TestFadeScoreIsCausalReproducibleAndScaleFree(t *testing.T) {
	in := buyFade()
	_, base := scoreFade(t, in)
	future := in
	future.bars = append(append([]market.Candle(nil), in.bars...), market.Candle{Open: 90, High: 91, Low: 80, Close: 85})
	_, after := scoreFade(t, future)
	for name, v := range base {
		if after[name] != v {
			t.Fatalf("a later bar changed %s: %v -> %v", name, v, after[name])
		}
	}
	for _, k := range []float64{1000, 0.0001} {
		s := in
		s.bars = make([]market.Candle, len(in.bars))
		for i, b := range in.bars {
			s.bars[i] = market.Candle{Open: b.Open * k, High: b.High * k, Low: b.Low * k, Close: b.Close * k}
		}
		s.pool.Level, s.pool.Band = in.pool.Level*k, in.pool.Band*k
		s.atr, s.price, s.reference, s.invalidation, s.objective = in.atr*k, in.price*k, in.reference*k, in.invalidation*k, in.objective*k
		_, got := scoreFade(t, s)
		for name, want := range base {
			if strings.HasPrefix(name, "score_") && math.Abs(got[name]-want) > 1e-9 {
				t.Errorf("scale %v: %s = %v, want %v", k, name, got[name], want)
			}
		}
	}
}
