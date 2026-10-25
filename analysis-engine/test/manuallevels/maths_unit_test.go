package manuallevels_test

import (
	"math"
	"testing"
)

func f(v float64) *float64 { return &v }

func TestProximalEntryIsTheEdgePriceReachesFirstAndMirrors(t *testing.T) {
	if got := proximalEntry("BUY", 4141, f(4144)); got != 4144 {
		t.Errorf("BUY proximal %v, want the top 4144", got)
	}
	if got := proximalEntry("SELL", 4154, f(4157)); got != 4154 {
		t.Errorf("SELL proximal %v, want the bottom 4154", got)
	}
	if got := proximalEntry("BUY", 4144, f(4141)); got != 4144 { // the band may be written either way round
		t.Errorf("BUY with entry above entry_end: %v", got)
	}
	if got := proximalEntry("SELL", 4100, nil); got != 4100 {
		t.Errorf("a missing entry_end is a single price, got %v", got)
	}
}

func TestStopIsTheOriginalStopWhenTheOwnerMovedIt(t *testing.T) {
	if s, ok := stopPrice(f(4139), f(4143.01)); !ok || s != 4139 {
		t.Errorf("got %v %v, want original_sl 4139", s, ok)
	}
	if s, ok := stopPrice(nil, f(4143.01)); !ok || s != 4143.01 {
		t.Errorf("got %v %v, want sl", s, ok)
	}
	if _, ok := stopPrice(nil, nil); ok {
		t.Error("no stop at all must say so")
	}
}

func TestDistanceIsZeroInsideTheBandAndTheGapToTheNearestEdgeOutside(t *testing.T) {
	band := structure{Low: 100, High: 102}
	for price, want := range map[float64]float64{100: 0, 101: 0, 102: 0, 99.5: 0.5, 103: 1} {
		if got := distance(price, band); math.Abs(got-want) > 1e-12 {
			t.Errorf("distance(%v) = %v, want %v", price, got, want)
		}
	}
}

func TestAFamilyExplainsAnEntryInsideOrWithinAQuarterAtr(t *testing.T) {
	const atr = 4.0
	if !explains(0, atr) || !explains(1.0, atr) {
		t.Error("inside, and exactly 0.25 ATR away, must explain")
	}
	if explains(1.01, atr) || explains(0, 0) {
		t.Error("beyond 0.25 ATR, or without an ATR, must not explain")
	}
}

func TestNearestPicksTheClosestStructureOfTheFamily(t *testing.T) {
	list := []structure{{Family: "swing", TF: "H1", Low: 90, High: 91}, {Family: "swing", TF: "M5", Low: 99, High: 99.4}, {Family: "swing", TF: "M5", Low: 120, High: 121}}
	got, d, ok := nearest(100, list)
	if !ok || got.Low != 99 || math.Abs(d-0.6) > 1e-12 {
		t.Fatalf("got %+v %v %v", got, d, ok)
	}
	if _, _, ok := nearest(100, nil); ok {
		t.Error("an empty family has no nearest structure")
	}
}

func TestTheStopProtectsTheNearestStructureOnTheEntrySideAndMirrors(t *testing.T) {
	// BUY entering at 105 with the stop at 95: the structures between them are protected; the closest to the stop is the anchor.
	buy := []structure{{Low: 96, High: 98}, {Low: 100, High: 101}, {Low: 90, High: 92}, {Low: 110, High: 112}}
	got, gap, ok := protectedBy("BUY", 105, 95, buy)
	if !ok || got.Low != 96 || math.Abs(gap-1) > 1e-12 {
		t.Fatalf("BUY: got %+v gap %v ok %v, want the 96-98 structure, gap 1", got, gap, ok)
	}
	// SELL mirrored: entry 95, stop 105, the structure just under the stop.
	sell := []structure{{Low: 102, High: 104}, {Low: 98, High: 99}, {Low: 108, High: 109}, {Low: 80, High: 82}}
	got, gap, ok = protectedBy("SELL", 95, 105, sell)
	if !ok || got.High != 104 || math.Abs(gap-1) > 1e-12 {
		t.Fatalf("SELL: got %+v gap %v ok %v, want the 102-104 structure, gap 1", got, gap, ok)
	}
	if _, _, ok := protectedBy("BUY", 105, 95, []structure{{Low: 90, High: 92}}); ok {
		t.Error("a structure beyond the stop is not protected by it")
	}
}

func TestNearestBeyondTheStopMirrors(t *testing.T) {
	buy := []structure{{Low: 90, High: 92}, {Low: 80, High: 82}, {Low: 96, High: 98}}
	got, gap, ok := nearestBeyond("BUY", 95, buy)
	if !ok || got.High != 92 || math.Abs(gap-3) > 1e-12 {
		t.Fatalf("BUY: got %+v gap %v ok %v", got, gap, ok)
	}
	sell := []structure{{Low: 108, High: 109}, {Low: 120, High: 121}, {Low: 100, High: 104}}
	got, gap, ok = nearestBeyond("SELL", 105, sell)
	if !ok || got.Low != 108 || math.Abs(gap-3) > 1e-12 {
		t.Fatalf("SELL: got %+v gap %v ok %v", got, gap, ok)
	}
}

func TestAcceptanceNeedsTwoConsecutiveClosesBeyondTheLevelAgainstTheTrade(t *testing.T) {
	level := structure{Low: 100, High: 101}
	if acceptedBeyond("BUY", level, []float64{102, 99, 103, 99, 102}) {
		t.Error("two non-consecutive closes below are a wick, not acceptance")
	}
	if !acceptedBeyond("BUY", level, []float64{102, 99.5, 99, 102}) {
		t.Error("two consecutive closes below a BUY level are acceptance (a flip)")
	}
	if !acceptedBeyond("SELL", level, []float64{100.5, 101.5, 102, 100}) {
		t.Error("two consecutive closes above a SELL level are acceptance")
	}
	if acceptedBeyond("SELL", level, []float64{99, 98, 99.5}) {
		t.Error("closes on the trade's own side are not beyond the level against it")
	}
}
