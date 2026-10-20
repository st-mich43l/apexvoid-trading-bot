package techniquezone

import "testing"

func TestAnInstanceCanBeTouchedOnlyAfterTheBarThatCompletedIt(t *testing.T) {
	// A fair value gap is anchored on its third candle; a supply/demand zone is
	// complete when its displacement leg ends, an order block at its BOS.
	fvg := newZone(10, 11, "demand", 7, "fvg")
	if got := instanceFromZone(fvg, "fvg", 0).TouchableFrom; got != 8 {
		t.Fatalf("fvg touchable from %d, want 8", got)
	}
	leg := newZone(10, 11, "supply", 4, "supply_demand")
	leg.BreakIndex = 9
	if got := instanceFromZone(leg, "supply_demand", 0).TouchableFrom; got != 10 {
		t.Fatalf("supply/demand touchable from %d, want the bar after its leg (10)", got)
	}
}
