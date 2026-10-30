package demand

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// path is a piecewise-linear price path, `per` bars between consecutive turning points, drawn as M15 candles with a 0.3 range around the close.
func path(per int, points ...float64) []market.Candle {
	var bars []market.Candle
	t := int64(1_792_000_000)
	prev := points[0]
	for _, p := range points[1:] {
		for i := 1; i <= per; i++ {
			close := prev + (p-prev)*float64(i)/float64(per)
			bars = append(bars, market.Candle{Time: t, Open: close, High: close + 0.15, Low: close - 0.15, Close: close, Volume: 1})
			t += 900
		}
		prev = p
	}
	return bars
}

func unitATR(n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = 1
	}
	return out
}

func TestTheDealingRangeFollowsARallyThatBreaksEverySwing(t *testing.T) {
	// 10 -> 20 (swing high) -> 15 (swing low) -> 30: price is above every earlier swing pair
	bars := path(5, 10, 20, 15, 30)
	d := dealingRange(bars, unitATR(len(bars)), market.M15, nil, 2)
	if !d.OK || math.Abs(d.Low-14.85) > 1e-9 || math.Abs(d.High-30.15) > 1e-9 {
		t.Fatalf("range %+v, want [last swing low 15, running high 30]", d)
	}
	if eq := d.equilibrium(); math.Abs(eq-22.5) > 1e-9 {
		t.Fatalf("equilibrium %v", eq)
	}
	// a supply zone at the running high is premium, a with-trend pullback in the lower half is discount: both sides judged on the same range
	if !(28 >= d.equilibrium()) || !(17 <= d.equilibrium()) {
		t.Fatal("a zone at 28 is above the equilibrium and one at 17 below it")
	}
}

func TestTheDealingRangeOfARangeIsTheLastSwingPair(t *testing.T) {
	bars := path(5, 10, 20, 12, 19, 13, 18)
	d := dealingRange(bars, unitATR(len(bars)), market.M15, nil, 2)
	if !d.OK || math.Abs(d.High-19.15) > 1e-9 || math.Abs(d.Low-12.85) > 1e-9 {
		t.Fatalf("range %+v, want the last confirmed swing high 19 and low 13 -> [12.85 ... 19.15]", d)
	}
}

func TestTheDealingRangeFollowsASellOff(t *testing.T) {
	bars := path(5, 30, 20, 25, 10)
	d := dealingRange(bars, unitATR(len(bars)), market.M15, nil, 2)
	if !d.OK || math.Abs(d.High-25.15) > 1e-9 || math.Abs(d.Low-9.85) > 1e-9 {
		t.Fatalf("range %+v, want [running low 10, last swing high 25]", d)
	}
}

func TestTheM5BarsSinceTheLastClosedStructureBarExtendTheRange(t *testing.T) {
	bars := path(5, 10, 20, 15, 18)
	closed := bars[len(bars)-1].Time + 900
	m5 := []market.Candle{
		{Time: closed - 300, Open: 18, High: 99, Low: 1, Close: 18}, // inside the last structure bar: already part of it, never read
		{Time: closed, Open: 18, High: 21.5, Low: 17.9, Close: 21},
		{Time: closed + 300, Open: 21, High: 22.5, Low: 20.8, Close: 22},
	}
	d := dealingRange(bars, unitATR(len(bars)), market.M15, m5, 2)
	if !d.OK || math.Abs(d.High-22.5) > 1e-9 {
		t.Fatalf("range %+v, want the running high 22.5 of the M5 bars since the structure bar closed", d)
	}
}

// Causality: the range is built from the closed bars it is given and nothing else. A swing needs pivotBars closed bars after it, so the top of a leg that has
// not been followed by two lower bars is not a swing yet; its bars still extend the range as the running extreme.
func TestTheDealingRangeReadsOnlyConfirmedSwingsAndTheRunningExtreme(t *testing.T) {
	bars := path(5, 10, 20, 15, 30)
	cut := dealingRange(bars[:len(bars)-1], unitATR(len(bars)-1), market.M15, nil, 2)
	if !cut.OK || cut.High < 27 {
		t.Fatalf("range %+v: the running high of the unconfirmed leg must be inside the range", cut)
	}
	// the rally's top is the last bar: no later bar confirms it, yet it is the range high (a running extreme, not a swing)
	if full := dealingRange(bars, unitATR(len(bars)), market.M15, nil, 2); full.High < 30 {
		t.Fatalf("range %+v: the last bar's high is the running high", full)
	}
	// no swing pair yet: no range, no points
	if flat := dealingRange(path(5, 10, 14), unitATR(5), market.M15, nil, 2); flat.OK {
		t.Fatalf("a straight move has no confirmed swing pair: %+v", flat)
	}
}

func TestPositionIsRelativeToTheRange(t *testing.T) {
	d := dealingBand{High: 30, Low: 10, OK: true}
	if p := d.position(25); math.Abs(p-0.75) > 1e-9 {
		t.Fatalf("position %v", p)
	}
	if !math.IsNaN((dealingBand{}).position(1)) {
		t.Fatal("no range, no position")
	}
}
