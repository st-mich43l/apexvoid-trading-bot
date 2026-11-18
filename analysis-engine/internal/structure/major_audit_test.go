package structure

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// SD-2 audit probes. Each of the "Defect" tests states what a major swing must be and failed on the classifier as merged in #886.

// legThenDrift builds: a decline into a structural low L, a slow rise (+0.5 ATR per bar) with an optional tiny one-bar dip near the top, a peak, and a fall that closes below the
// tiny dip's level but stays far above L. The returned indices are the structural low, the tiny dip pivot and the peak bar.
func legThenDrift(tinyDip bool) (closes []float64, low, dip, peak int) {
	price := 100.0
	for i := 0; i < 8; i++ {
		price -= 1.0
		closes = append(closes, price)
	}
	low = len(closes) - 1
	for i := 0; i < 30; i++ { // the climb out of L: +0.9 per bar with a 0.9 pullback every tenth bar (internal lows well under an ATR of prominence)
		if i%10 == 9 {
			price -= 0.9
		} else {
			price += 0.9
		}
		closes = append(closes, price)
	}
	for i := 0; i < 12; i++ {
		price += 0.5
		closes = append(closes, price)
		if tinyDip && i == 6 {
			price -= 0.9 // a one-bar dip well under an ATR: a raw pivot low, not structure
			closes = append(closes, price)
			dip = len(closes) - 1
		}
	}
	peak = len(closes) - 1
	closes = append(closes, price+0.4)
	peak = len(closes) - 1
	for i := 0; i < 6; i++ { // the fall: five-plus ATR, closing below the tiny dip but nowhere near L
		price = closes[len(closes)-1] - 1.0
		closes = append(closes, price)
	}
	return closes, low, dip, peak
}

func auditSwing(t *testing.T, closes []float64, kind PivotKind, index int) (MajorSwing, []market.Candle) {
	t.Helper()
	bars, atr := path(closes, 0.1)
	peakWick(bars, index)
	return findSwing(t, ClassifyMajor(bars, atr, majorTestConfig()), kind, index), bars
}

// Defect: the shift reference was the most recent RAW opposite pivot, so a one-bar dip near the top was "the structure" and closing below it was a CHoCH that, with an
// external high and a deep fall, made the swing MAJOR. An internal break does not promote a swing: the reference must be a significant swing low.
func TestAnInternalBreakDoesNotMakeASwingMajor(t *testing.T) {
	closes, _, _, peak := legThenDrift(true)
	s, _ := auditSwing(t, closes, PivotHigh, peak)
	if s.Class == SwingMajor || s.Shift != ShiftNone {
		t.Fatalf("closing below a one-bar dip is not a structure break: %s", brief(s))
	}
}

// The same market without the dip: the fall never closes through a significant low either, so it is not major (control: the rule is about significance, not about dips).
func TestAFallThatKeepsTheStructuralLowIsNotAShift(t *testing.T) {
	closes, _, _, peak := legThenDrift(false)
	s, _ := auditSwing(t, closes, PivotHigh, peak)
	if s.Shift != ShiftNone {
		t.Fatalf("the structural low was never closed through: %s", brief(s))
	}
}

// A real break: the fall closes through the structural low (the internal pullback lows do not count, the structural one does).
func TestClosingThroughTheStructuralLowIsTheShift(t *testing.T) {
	closes, low, _, peak := legThenDrift(false)
	price := closes[len(closes)-1]
	for price > closes[low]-1 {
		price -= 2.0
		closes = append(closes, price)
	}
	s, _ := auditSwing(t, closes, PivotHigh, peak)
	if s.Shift == ShiftNone || s.Class != SwingMajor {
		t.Fatalf("a close through the structural low is the shift of an external high: %s", brief(s))
	}
}

// Defect: a significant low that price had already closed through (before the swing formed) was still the reference whenever the newer lows were skipped as insignificant, so a
// later close below it counted as a fresh break. A broken level is not structure any more.
func TestALevelAlreadyBrokenBeforeTheSwingIsNotAFreshBreak(t *testing.T) {
	var c []float64
	price := 100.0
	step := func(n int, d float64) {
		for i := 0; i < n; i++ {
			price += d
			c = append(c, price)
		}
	}
	step(6, -1.0)  // down to L1 (94): a significant low once the rally has happened
	step(10, +1.0) // rally to 104
	step(11, -1.0) // down to 93: closes through L1 (94), so L1 is broken
	step(2, +0.3)  // a bounce well under an ATR of prominence: the new low is NOT significant
	step(7, -1.0)  // falls again below the bounce's low
	bars, atr := path(c, 0.1)
	broke := false
	for _, s := range ClassifyMajor(bars, atr, majorTestConfig()) {
		if s.Kind == PivotHigh && s.BarIndex > 18 && s.Shift != ShiftNone {
			broke = true
			t.Errorf("a swing high after the collapse took a shift from a level that was already broken or is not structure: %s level %.2f", brief(s), s.ShiftLevel)
		}
	}
	_ = broke
}

// Defect: equal highs both took the break of the same low as their own shift: one broken level counted as independent evidence for two swings. A broken level is credited once,
// to the highest swing high between the level and the break (the earliest when equal).
func TestOneBrokenLevelIsCreditedToOneSwing(t *testing.T) {
	var c []float64
	price := 100.0
	step := func(n int, d float64) {
		for i := 0; i < n; i++ {
			price += d
			c = append(c, price)
		}
	}
	step(8, -1.0)  // a decline into the structural low
	step(14, +1.0) // up to the first high
	first := len(c) - 1
	step(3, -0.1) // a dip too shallow to be structure: both highs share the structural low as their reference
	step(3, +0.1) // back to an equal high
	second := len(c) - 1
	step(20, -1.0) // the fall through the low
	bars, atr := path(c, 0.1)
	peakWick(bars, first)
	bars[second].High = bars[first].High
	swings := ClassifyMajor(bars, atr, majorTestConfig())
	a, b := findSwing(t, swings, PivotHigh, first), findSwing(t, swings, PivotHigh, second)
	credited := 0
	for _, s := range []MajorSwing{a, b} {
		if s.Shift != ShiftNone {
			credited++
		}
	}
	if credited != 1 || a.Shift == ShiftNone {
		t.Fatalf("the same close through the same low is evidence once, not for both equal highs: %s / %s", brief(a), brief(b))
	}
}

// Defect: CHoCH/BOS were told apart by the previous RAW high, so a lower high than a one-bar fractal was a "BOS" in a rising market. The prior state is the previous SIGNIFICANT swing of
// the same kind.
func TestTheShiftLabelUsesTheSignificantPriorStructureNotATinyFractal(t *testing.T) {
	closes, low, _, peak := legThenDrift(false)
	price := closes[len(closes)-1]
	for price > closes[low]-1 {
		price -= 2.0
		closes = append(closes, price)
	}
	s, _ := auditSwing(t, closes, PivotHigh, peak)
	if s.Shift != ShiftCHoCH {
		t.Fatalf("a higher high than every significant high before it, broken through the structural low, is a change of character: %s", brief(s))
	}
}

// ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// Properties the classifier already has and must keep.

// The reference level of a break is confirmed before the break can happen: a close below a pivot low is impossible inside its own right window, so no shift is dated before it.
func TestAShiftIsNeverDatedBeforeItsReferenceWasConfirmed(t *testing.T) {
	closes, low, _, peak := legThenDrift(false)
	price := closes[len(closes)-1]
	for price > closes[low]-1 {
		price -= 2.0
		closes = append(closes, price)
	}
	bars, atr := path(closes, 0.1)
	peakWick(bars, peak)
	for _, s := range ClassifyMajor(bars, atr, majorTestConfig()) {
		if s.Shift != ShiftNone && s.ShiftAt <= s.ConfirmedAt {
			t.Fatalf("a shift dated at or before the swing's own confirmation: %s", brief(s))
		}
	}
}

// Liquidity sweep: a wick above equal highs that closes back and falls is still a genuine external swing (the sweep extreme IS the reversal high); a failed breakout (closes above
// for a few bars, then falls) is major too. Neither is mistaken for the other's relevance: the first is never exceeded, the second's earlier high was closed through.
func TestSweepExtremeAndFailedBreakoutAreBothReadAsWhatTheyAre(t *testing.T) {
	// Sweep: equal-ish highs, then one bar pokes 0.3 ATR above and closes back below, then a deep fall through the low.
	build := func(accept bool) ([]market.Candle, []float64, int) {
		var c []float64
		price := 100.0
		step := func(n int, d float64) {
			for i := 0; i < n; i++ {
				price += d
				c = append(c, price)
			}
		}
		step(8, -1.0)
		step(14, +1.0)
		step(3, -0.6)
		step(3, +0.6) // retest of the high
		sweep := len(c)
		if accept {
			step(3, +0.5) // closes above the old high for three bars...
			sweep = len(c) - 1
		} else {
			c = append(c, price-0.2)
		}
		step(16, -1.0)
		bars, atr := path(c, 0.1)
		return bars, atr, sweep
	}
	for _, accept := range []bool{false, true} {
		bars, atr, at := build(accept)
		best := 0
		for i, b := range bars {
			if b.High > bars[best].High {
				best = i
			}
		}
		peakWick(bars, best)
		s := findSwing(t, ClassifyMajor(bars, atr, majorTestConfig()), PivotHigh, best)
		if !s.External || s.Class != SwingMajor || !s.Relevant {
			t.Fatalf("accept=%v (bar %d, extreme %d): the reversal extreme is an external swing: %s", accept, at, best, brief(s))
		}
	}
}

// Prefix replay: at every prefix the classifier returns only what the closed bars of that prefix show, an identity once seen never changes, and the information that earned a class
// (the shift, the departure) is dated no later than the last bar of the prefix.
func TestPrefixReplayNeverUsesAFutureBar(t *testing.T) {
	closes, low, _, peak := legThenDrift(true)
	price := closes[len(closes)-1]
	for price > closes[low]-1 {
		price -= 2.0
		closes = append(closes, price)
	}
	bars, atr := path(closes, 0.1)
	peakWick(bars, peak)
	cfg := majorTestConfig()
	for n := 12; n <= len(bars); n++ {
		got := ClassifyMajor(bars[:n], atr[:n], cfg)
		// The same prefix classified from a copy that cannot see anything beyond it must agree exactly (no hidden dependence on the full slice).
		again := ClassifyMajor(append([]market.Candle(nil), bars[:n]...), append([]float64(nil), atr[:n]...), cfg)
		if len(got) != len(again) {
			t.Fatalf("prefix %d: %d vs %d swings", n, len(got), len(again))
		}
		for i := range got {
			if got[i] != again[i] {
				t.Fatalf("prefix %d: swing %d differs between two reads of the same bars", n, i)
			}
			if s := got[i]; s.ConfirmedAt > bars[n-1].Time || s.ShiftAt > bars[n-1].Time {
				t.Fatalf("prefix %d: %s is dated after the last closed bar", n, brief(s))
			}
		}
	}
}

// Truncation: dropping bars from the start of the window can only remove evidence. While the reference of the shift and the swing's left side stay in the window the class is
// unchanged; once the window evicts the reference the swing can fall from MAJOR to INTERMEDIATE (it never needs the reference for that), and it never rises.
func TestTruncationOnlyEverRemovesEvidence(t *testing.T) {
	closes, low, _, peak := legThenDrift(false)
	price := closes[len(closes)-1]
	for price > closes[low]-1 {
		price -= 2.0
		closes = append(closes, price)
	}
	bars, atr := path(closes, 0.1)
	peakWick(bars, peak)
	cfg := majorTestConfig()
	full := findSwing(t, ClassifyMajor(bars, atr, cfg), PivotHigh, peak)
	refIndex := -1
	for i, c := range bars {
		if c.Time == full.ShiftLevelTime {
			refIndex = i
		}
	}
	if full.Class != SwingMajor || refIndex < 0 {
		t.Fatalf("setup: %s", brief(full))
	}
	for cut := 1; cut+cfg.ExternalBars < peak; cut++ {
		got := findSwing(t, ClassifyMajor(bars[cut:], atr[cut:], cfg), PivotHigh, peak-cut)
		if got.Class > full.Class {
			t.Fatalf("a shorter window raised the class: %s vs %s", brief(got), brief(full))
		}
		if cut+cfg.PivotLeftBars <= refIndex && got.Class != full.Class {
			t.Fatalf("the reference is still in a window starting %d bars later, the class must not change: %s vs %s", cut, brief(got), brief(full))
		}
	}
}

// Missing and flat data: bars that are missing, leading zero or NaN ATR (initialisation) and equal prices never panic, never produce a swing without a volatility read and are
// deterministic.
func TestMissingBarsAndATRInitialisationAreHandled(t *testing.T) {
	closes, _, _, peak := legThenDrift(false)
	bars, atr := path(closes, 0.1)
	peakWick(bars, peak)
	cfg := majorTestConfig()
	atr2 := append([]float64(nil), atr...)
	for i := 0; i < 6; i++ {
		atr2[i] = 0
	}
	atr2[7] = math.NaN()
	for _, s := range ClassifyMajor(bars, atr2, cfg) {
		if s.BarIndex < 8 && (s.BarIndex < 6 || s.BarIndex == 7) {
			t.Fatalf("a swing at bar %d has no volatility read: %s", s.BarIndex, brief(s))
		}
	}
	// A hole in the feed: three bars missing near the middle; identity is by time, so the peak is still found with its own time.
	holed := append(append([]market.Candle(nil), bars[:20]...), bars[23:]...)
	holedATR := append(append([]float64(nil), atr[:20]...), atr[23:]...)
	found := false
	for _, s := range ClassifyMajor(holed, holedATR, cfg) {
		if s.Kind == PivotHigh && s.Time == bars[peak].Time {
			found = true
		}
	}
	if !found {
		t.Fatal("the peak must survive a hole in the feed, identified by its own time")
	}
	// Flat equal prices: no pivot, no swing, no panic.
	flat := make([]market.Candle, 40)
	flatATR := make([]float64, 40)
	for i := range flat {
		flat[i] = market.Candle{Time: int64(1_800_000_000 + i*stepSeconds), Open: 100, High: 100, Low: 100, Close: 100}
		flatATR[i] = 1
	}
	if got := ClassifyMajor(flat, flatATR, cfg); len(got) != 0 {
		t.Fatalf("equal prices have no pivots: %d", len(got))
	}
}

// The classifier is a pure function: a restart that reloads the same closed bars reads the same swings.
func TestARestartReadsTheSameSwings(t *testing.T) {
	closes, _, _, peak := legThenDrift(true)
	bars, atr := path(closes, 0.1)
	peakWick(bars, peak)
	a, b := ClassifyMajor(bars, atr, majorTestConfig()), ClassifyMajor(append([]market.Candle(nil), bars...), append([]float64(nil), atr...), majorTestConfig())
	if len(a) != len(b) {
		t.Fatal("different swing counts")
	}
	for i := range a {
		if a[i] != b[i] {
			t.Fatalf("swing %d differs", i)
		}
	}
}

// A swing's class falls when the evidence that supported it is invalidated, and only then: a close above the high ends its relevance but never rewrites what it was.
func TestALaterCloseBeyondTheSwingEndsRelevanceNotTheClass(t *testing.T) {
	closes, low, _, peak := legThenDrift(false)
	price := closes[len(closes)-1]
	for price > closes[low]-1 {
		price -= 2.0
		closes = append(closes, price)
	}
	before, _ := auditSwing(t, closes, PivotHigh, peak)
	for i := 0; i < 45; i++ {
		price += 1.5
		closes = append(closes, price)
	}
	after, _ := auditSwing(t, closes, PivotHigh, peak)
	if after.Relevant || !after.Exceeded {
		t.Fatalf("a close above the high ends its relevance: %s", brief(after))
	}
	if before.Class != SwingMajor || after.Class == SwingMinor {
		t.Fatalf("before %s, after %s", brief(before), brief(after))
	}
}

// The corrected shift logic has an exact price mirror: a swing low with the same structure reads exactly as the swing high does.
func TestTheShiftLogicHasAPriceMirror(t *testing.T) {
	closes, low, _, peak := legThenDrift(true)
	price := closes[len(closes)-1]
	for price > closes[low]-1 {
		price -= 2.0
		closes = append(closes, price)
	}
	bars, atr := path(closes, 0.1)
	peakWick(bars, peak)
	mirror := make([]market.Candle, len(bars))
	for i, c := range bars {
		mirror[i] = market.Candle{Time: c.Time, Open: 300 - c.Open, High: 300 - c.Low, Low: 300 - c.High, Close: 300 - c.Close}
	}
	x, y := ClassifyMajor(bars, atr, majorTestConfig()), ClassifyMajor(mirror, atr, majorTestConfig())
	if len(x) != len(y) {
		t.Fatalf("%d swings against %d", len(x), len(y))
	}
	flip := map[PivotKind]PivotKind{PivotHigh: PivotLow, PivotLow: PivotHigh}
	for _, s := range x {
		m := findSwing(t, y, flip[s.Kind], s.BarIndex)
		if m.Class != s.Class || m.Shift != s.Shift || m.ShiftAt != s.ShiftAt || m.ShiftLevelTime != s.ShiftLevelTime || math.Abs(m.ShiftLevel-(300-s.ShiftLevel)) > 1e-9 && s.ShiftLevel != 0 || m.External != s.External {
			t.Fatalf("a swing and its mirror must read alike: %s vs %s", brief(s), brief(m))
		}
	}
}
