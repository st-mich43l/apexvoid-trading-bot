package quality

import (
	"math"
	"testing"
)

func near(t *testing.T, name string, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-9 {
		t.Errorf("%s = %v, want %v", name, got, want)
	}
}

// A. Direction-aware location. The reference is the proximal edge of the published band: a BUY's high, a SELL's low.
func TestEntryChaseIsMeasuredInTheTradesDirection(t *testing.T) {
	const atr = 2.0
	cases := []struct {
		name       string
		buy        bool
		price, ref float64
		chase, fav float64 // in ATR
		score      float64
	}{
		{"BUY price above the entry is a chase", true, 101.0, 100.0, 0.5, 0, Fall(0.5, 0.25, 1.5)},
		{"BUY price deeper in the band is not a chase", true, 99.0, 100.0, 0, 0.5, 1},
		{"BUY price at the entry is not a chase", true, 100.0, 100.0, 0, 0, 1},
		{"SELL price below the entry is a chase", false, 99.0, 100.0, 0.5, 0, Fall(0.5, 0.25, 1.5)},
		{"SELL price higher in the band is not a chase", false, 101.0, 100.0, 0, 0.5, 1},
		{"SELL price at the entry is not a chase", false, 100.0, 100.0, 0, 0, 1},
		{"BUY a long chase scores zero", true, 103.0, 100.0, 1.5, 0, 0},
		{"BUY exactly at the lower bound of the curve", true, 100.5, 100.0, 0.25, 0, 1},
	}
	for _, c := range cases {
		chase, fav, ok := EntryChase(c.buy, c.price, c.ref, atr)
		if !ok {
			t.Fatalf("%s: not measurable", c.name)
		}
		near(t, c.name+" chase", chase, c.chase)
		near(t, c.name+" favourable", fav, c.fav)
		near(t, c.name+" score", EntryLocation(c.buy, c.price, c.ref, atr), c.score)
	}
}

func TestEntryLocationIsZeroWithoutMeasurableGeometry(t *testing.T) {
	for _, atr := range []float64{0, -1, math.NaN(), math.Inf(1)} {
		if got := EntryLocation(true, 100, 100, atr); got != 0 {
			t.Errorf("atr %v: score %v, want 0", atr, got)
		}
		if _, _, ok := EntryChase(true, 100, 100, atr); ok {
			t.Errorf("atr %v: must not be measurable", atr)
		}
	}
	for _, v := range []float64{math.NaN(), math.Inf(-1)} {
		if EntryLocation(true, v, 100, 1) != 0 || EntryLocation(false, 100, v, 1) != 0 {
			t.Errorf("a non-finite price or reference (%v) must score 0", v)
		}
	}
}

// B. Effective risk. The structural stop is never moved; only the denominator the room is judged against.
func TestEffectiveRiskIsTheLargerOfTheStructuralStopAndTheExecutionFloor(t *testing.T) {
	const pip = 0.0001
	cases := []struct {
		name                        string
		buy                         bool
		structuralPips, floorPips   float64
		targetPips                  float64
		wantEffectivePips, wantRoom float64
	}{
		{"the example: a 6-pip structure under a 15-pip floor, 30 pips of room is 2R, not 5R", true, 6, 15, 30, 15, 2},
		{"a structural stop wider than the floor stands", true, 20, 15, 60, 20, 3},
		{"a risk exactly equal to the floor", true, 15, 15, 45, 15, 3},
		{"no configured floor: the structural stop", true, 6, 0, 30, 6, 5},
		{"the same on the SELL side", false, 6, 15, 30, 15, 2},
	}
	for _, c := range cases {
		ref := 1.1000
		inv := ref - c.structuralPips*pip
		target := ref + c.targetPips*pip
		if !c.buy {
			inv = ref + c.structuralPips*pip
			target = ref - c.targetPips*pip
		}
		g := MeasureRoom(c.buy, ref, inv, target, c.floorPips*pip, true)
		if !g.Valid {
			t.Fatalf("%s: invalid", c.name)
		}
		near(t, c.name+" structural risk", g.StructuralRisk/pip, c.structuralPips)
		near(t, c.name+" effective risk", g.EffectiveRisk/pip, c.wantEffectivePips)
		near(t, c.name+" effective room R", g.EffectiveRoomR, c.wantRoom)
		near(t, c.name+" structural room R", g.StructuralRoomR, c.targetPips/c.structuralPips)
		near(t, c.name+" score", g.Score, Room(c.targetPips*pip, c.wantEffectivePips*pip, true))
	}
}

func TestInvalidRiskGeometryScoresZero(t *testing.T) {
	cases := map[string]RoomGeometry{
		"BUY invalidation above the entry":  MeasureRoom(true, 100, 101, 110, 0, true),
		"SELL invalidation below the entry": MeasureRoom(false, 100, 99, 90, 0, true),
		"zero risk":                         MeasureRoom(true, 100, 100, 110, 0, true),
		"NaN invalidation":                  MeasureRoom(true, 100, math.NaN(), 110, 0, true),
		"Inf target":                        MeasureRoom(true, 100, 99, math.Inf(1), 0, true),
		"NaN floor":                         MeasureRoom(true, 100, 99, 110, math.NaN(), true),
	}
	for name, g := range cases {
		if g.Valid || g.Score != 0 || g.EffectiveRisk != 0 || g.EffectiveRoomR != 0 {
			t.Errorf("%s: %+v, want invalid, no risk and a score of 0", name, g)
		}
	}
}

// C. A target is room only when it is ahead of the entry and observed structure.
func TestRoomNeedsATargetAheadOfTheEntryThatIsObservedStructure(t *testing.T) {
	// BUY at 100, structural stop 98 (2 points of risk), no floor.
	ahead := MeasureRoom(true, 100, 98, 106, 0, true) // 3R of real structure
	near(t, "3R structure scores", ahead.Score, 1)
	fixed := MeasureRoom(true, 100, 98, 106, 0, false) // the same price as a fixed reward:risk
	if !fixed.Valid || fixed.Score != 0 || fixed.EffectiveRoomR != 3 {
		t.Errorf("a fixed reward:risk target: %+v, want geometry published but no structural score", fixed)
	}
	for name, target := range map[string]float64{"behind the entry": 94, "at the entry": 100} {
		g := MeasureRoom(true, 100, 98, target, 0, true)
		if g.Valid || g.Score != 0 || g.EffectiveRoomR != 0 || g.Reward > 0 {
			t.Errorf("BUY target %s: %+v, want no room (a target behind the entry is never a reward)", name, g)
		}
	}
	if g := MeasureRoom(false, 100, 102, 106, 0, true); g.Valid || g.Score != 0 {
		t.Errorf("SELL target above the entry: %+v, want no room", g)
	}
	// The distance behind the entry must not appear as positive reward.
	if g := MeasureRoom(true, 100, 98, 94, 0, true); g.Reward >= 0 {
		t.Errorf("reward %v for a BUY target 6 behind the entry, want it signed negative", g.Reward)
	}
}

func TestRoomScoreIsBoundedAndMonotone(t *testing.T) {
	prev := -1.0
	for r := 0.0; r <= 6; r += 0.25 {
		g := MeasureRoom(true, 100, 98, 100+2*r, 0, true)
		if g.Score < 0 || g.Score > 1 || g.Score < prev {
			t.Errorf("R %.2f: score %v (previous %v)", r, g.Score, prev)
		}
		prev = g.Score
	}
}
