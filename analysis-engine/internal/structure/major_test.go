package structure

import (
	"fmt"
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func majorTestConfig() MajorConfig {
	return MajorConfig{
		PivotLeftBars: 2, PivotRightBars: 2, MajorProminenceATR: 3, IntermediateProminenceATR: 1.5, MajorDepartureATR: 2, IntermediateDepartureATR: 1,
		DepartureBars: 8, ExternalBars: 20, RelevanceBars: 150,
	}
}

const stepSeconds = 900

// path builds bars from a list of closes: each bar opens at the previous close, spans its open and close by `wick` on both sides, ATR 1.0.
func path(closes []float64, wick float64) ([]market.Candle, []float64) {
	bars := make([]market.Candle, len(closes))
	atr := make([]float64, len(closes))
	open := closes[0]
	for i, c := range closes {
		w := wick * (1 + 0.3*float64((i*7)%5)/5) // deterministic wick variation: real bars do not tie their highs and lows
		low := math.Min(open, c) - w
		if c < open {
			low -= 3 * wick // a down bar trades below its close: it is a real low, not a tie with the next bar's open
		}
		bars[i] = market.Candle{Time: int64(1_800_000_000 + i*stepSeconds), Open: open, High: math.Max(open, c) + w, Low: low, Close: c}
		atr[i] = 1.0
		open = c
	}
	return bars, atr
}

// uptrendThenReversal: 40 bars of three up bars and a sharp pullback (so there are real internal swing highs and lows, each group a little higher), the peak, then a
// fast fall that closes through the last higher low.
func uptrendThenReversal() []float64 {
	var c []float64
	price := 100.0
	for i := 0; i < 40; i++ {
		if i%4 == 3 {
			price -= 2.5
		} else {
			price += 1.0
		}
		c = append(c, price)
	}
	c = append(c, price+0.7) // the peak bar's close
	for i := 0; i < 10; i++ {
		price = c[len(c)-1] - 0.9
		c = append(c, price)
	}
	return c
}

// peakWick gives the peak bar the upper wick of a real rejection, so the bar after it (which opens at its close) does not tie its high.
func peakWick(bars []market.Candle, index int) {
	bars[index].High += 0.3
}

func findSwing(t *testing.T, swings []MajorSwing, kind PivotKind, index int) MajorSwing {
	t.Helper()
	for _, s := range swings {
		if s.Kind == kind && s.BarIndex == index {
			return s
		}
	}
	t.Fatalf("no %v swing at bar %d among %d swings", kind, index, len(swings))
	return MajorSwing{}
}

// peakIndex is the bar with the highest high.
func peakIndex(bars []market.Candle) int {
	best := 0
	for i, c := range bars {
		if c.High > bars[best].High {
			best = i
		}
	}
	return best
}

func brief(s MajorSwing) string {
	return fmt.Sprintf("%v@%d class=%s prom=%.2f dep=%.2f left=%d ext=%v shift=%q rel=%v", s.Kind, s.BarIndex, s.Class, s.ProminenceATR, s.DepartureATR, s.LeftDominanceBars, s.External, s.Shift, s.Relevant)
}

func TestAnExternalSwingWithAShiftIsMajor(t *testing.T) {
	bars, atr := path(uptrendThenReversal(), 0.1)
	at := peakIndex(bars)
	peakWick(bars, at)
	swings := ClassifyMajor(bars, atr, majorTestConfig())
	peak := findSwing(t, swings, PivotHigh, at)
	if peak.Class != SwingMajor {
		t.Fatalf("class %s: %+v", peak.Class, peak)
	}
	if !peak.External || peak.LeftDominanceBars < 20 {
		t.Fatalf("the peak held its high for the whole staircase: %s", brief(peak))
	}
	if peak.Shift != ShiftCHoCH || peak.ShiftAt == 0 || !peak.Relevant {
		t.Fatalf("the fall closed through the last higher low (an extension of the move, so a CHoCH) and the swing stands: %s", brief(peak))
	}
	if peak.ProminenceATR < 3 || peak.DepartureATR < 2 {
		t.Fatalf("measures: %s", brief(peak))
	}
	// Known only when the bar that completed the right window closed.
	if want := bars[at+majorTestConfig().PivotRightBars].Time; peak.ConfirmedAt != want {
		t.Fatalf("confirmed at %d, want the open of the second bar after the peak (%d)", peak.ConfirmedAt, want)
	}
}

func TestAnInternalPullbackHighIsNotMajor(t *testing.T) {
	bars, atr := path(uptrendThenReversal(), 0.1)
	at := peakIndex(bars)
	peakWick(bars, at)
	majors := 0
	for _, s := range ClassifyMajor(bars, atr, majorTestConfig()) {
		if s.Kind == PivotHigh && s.BarIndex < at && s.Class == SwingMajor {
			t.Fatalf("an internal high inside the staircase must not be major: %s", brief(s))
		}
		if s.Class == SwingMajor {
			majors++
		}
	}
	if majors == 0 {
		t.Fatal("the fixture's peak is the major swing")
	}
}

func TestEqualHighsAreNotBothExternal(t *testing.T) {
	// The first peak, a 5-ATR dip, then a second peak at exactly the same high: the second did not take out anything, and its left dominance stops at the first.
	base := uptrendThenReversal()
	first := peakIndex(func() []market.Candle { b, _ := path(base, 0.1); return b }())
	closes := append([]float64(nil), base[:first+1]...)
	peak := closes[len(closes)-1]
	for i := 0; i < 6; i++ {
		closes = append(closes, peak-0.9*float64(i+1))
	}
	for i := 0; i < 6; i++ {
		closes = append(closes, closes[len(closes)-1]+0.9)
	}
	second := len(closes) - 1
	for i := 0; i < 4; i++ {
		closes = append(closes, closes[len(closes)-1]-1.2)
	}
	bars, atr := path(closes, 0.1)
	peakWick(bars, first)
	bars[second].High = bars[first].High // exactly equal
	swings := ClassifyMajor(bars, atr, majorTestConfig())
	a, b := findSwing(t, swings, PivotHigh, first), findSwing(t, swings, PivotHigh, second)
	if !a.External {
		t.Fatalf("the first peak is the external high: %s", brief(a))
	}
	if b.External || b.LeftDominanceBars >= majorTestConfig().ExternalBars {
		t.Fatalf("an equal high is dominated by the first on its left, so it is not external: %s", brief(b))
	}
	if a.Exceeded {
		t.Fatalf("an equal high does not exceed the first: %s", brief(a))
	}
}

func TestClassificationIsVolatilityNormalised(t *testing.T) {
	closes := uptrendThenReversal()
	a, atrA := path(closes, 0.1)
	at := peakIndex(a)
	peakWick(a, at)
	// The same shape at ten times the price scale (XAU against FX is the real case): prices and ATR scale together.
	scaled := make([]float64, len(closes))
	for i, c := range closes {
		scaled[i] = 4300 + (c-100)*10
	}
	b, atrB := path(scaled, 1.0)
	b[at].High += 3.0 // the same upper wick at ten times the scale
	for i := range atrB {
		atrB[i] = 10
	}
	x, y := ClassifyMajor(a, atrA, majorTestConfig()), ClassifyMajor(b, atrB, majorTestConfig())
	if len(x) != len(y) {
		t.Fatalf("%d swings against %d", len(x), len(y))
	}
	for i := range x {
		if x[i].Class != y[i].Class || x[i].External != y[i].External || x[i].Shift != y[i].Shift || x[i].BarIndex != y[i].BarIndex {
			t.Fatalf("swing %d differs by scale: %s vs %s", i, brief(x[i]), brief(y[i]))
		}
		if math.Abs(x[i].ProminenceATR-y[i].ProminenceATR) > 1e-6 {
			t.Fatalf("prominence in ATR must not depend on the price scale: %s vs %s", brief(x[i]), brief(y[i]))
		}
	}
}

func TestClassificationHasAPriceMirror(t *testing.T) {
	bars, atr := path(uptrendThenReversal(), 0.1)
	peakWick(bars, peakIndex(bars))
	mirror := make([]market.Candle, len(bars))
	for i, c := range bars {
		mirror[i] = market.Candle{Time: c.Time, Open: 200 - c.Open, High: 200 - c.Low, Low: 200 - c.High, Close: 200 - c.Close}
	}
	x, y := ClassifyMajor(bars, atr, majorTestConfig()), ClassifyMajor(mirror, atr, majorTestConfig())
	if len(x) != len(y) {
		t.Fatalf("%d swings against %d", len(x), len(y))
	}
	flip := map[PivotKind]PivotKind{PivotHigh: PivotLow, PivotLow: PivotHigh}
	for _, s := range x {
		m := findSwing(t, y, flip[s.Kind], s.BarIndex)
		if m.Class != s.Class || m.Shift != s.Shift || m.External != s.External || m.Relevant != s.Relevant || math.Abs(m.ProminenceATR-s.ProminenceATR) > 1e-9 {
			t.Fatalf("a swing and its price mirror must classify alike: %s vs %s", brief(s), brief(m))
		}
	}
}

// Every swing is known only after its right window closed, and no fact about it comes from a later bar than the prefix given: the same swing is never moved
// or removed by appending bars, and its shift is dated no later than the last bar.
func TestSwingsAreCausalAndStable(t *testing.T) {
	bars, atr := path(uptrendThenReversal(), 0.1)
	peakWick(bars, peakIndex(bars))
	cfg := majorTestConfig()
	type key struct {
		kind  PivotKind
		index int
	}
	previous := map[key]int64{}
	for n := 10; n <= len(bars); n++ {
		seen := map[key]int64{}
		last := bars[n-1].Time
		for _, s := range ClassifyMajor(bars[:n], atr[:n], cfg) {
			if s.ConfirmedAt > last {
				t.Fatalf("prefix %d: %s confirmed after the last closed bar", n, brief(s))
			}
			if s.ShiftAt > last || s.BarIndex+cfg.PivotRightBars >= n {
				t.Fatalf("prefix %d: %s uses a bar that is not closed", n, brief(s))
			}
			seen[key{s.Kind, s.BarIndex}] = s.ConfirmedAt
		}
		for k, at := range previous {
			if got, ok := seen[k]; !ok || got != at {
				t.Fatalf("prefix %d: swing %+v changed or vanished when a bar was appended", n, k)
			}
		}
		previous = seen
	}
}

func TestASwingBrokenByACloseIsNoLongerRelevant(t *testing.T) {
	closes := uptrendThenReversal()
	for i := 0; i < 20; i++ { // price recovers and closes above the old peak
		closes = append(closes, closes[len(closes)-1]+0.9)
	}
	bars, atr := path(closes, 0.1)
	at := peakIndex(bars[:50])
	peakWick(bars, at)
	peak := findSwing(t, ClassifyMajor(bars, atr, majorTestConfig()), PivotHigh, at)
	if peak.Relevant || !peak.Exceeded {
		t.Fatalf("a close above the high ends its relevance: %s", brief(peak))
	}
}

func TestInvalidConfigurationClassifiesNothing(t *testing.T) {
	bars, atr := path(uptrendThenReversal(), 0.1)
	cfg := majorTestConfig()
	cfg.MajorProminenceATR = 1
	if got := ClassifyMajor(bars, atr, cfg); got != nil {
		t.Fatalf("a configuration whose major is below its intermediate must not classify: %d swings", len(got))
	}
}
