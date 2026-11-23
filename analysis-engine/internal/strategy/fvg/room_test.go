package fvg

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// v12: the room is judged against the risk execution actually takes (the structural stop, or the instrument floor when that is tighter), not the structural
// distance alone. The tests drive roomRisk and placeRoom, the two functions publish composes (an obstacle list in, the verdict out). Prices below are an FX pair
// (pip 0.0001, floor 12, cap 20) and gold (pip 0.1, floor 50, cap 70); every number is an exact pip count.

const (
	fxPip  = 0.0001
	xauPip = 0.1
)

func fxConfig() Config {
	return Config{MinimumTargetR: 1.0, FallbackTargetR: 2.0, StopBudget: strategyutil.StopBudget{MinPips: 12, MaxPips: 20, PipSize: fxPip}}
}

func xauConfig() Config {
	return Config{MinimumTargetR: 1.0, FallbackTargetR: 2.0, StopBudget: strategyutil.StopBudget{MinPips: 50, MaxPips: 70, PipSize: xauPip}}
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

// judge is what publish does: the risk basis, then the room. legacy judges the structural distance (v11).
func judge(c Config, buy bool, reference, invalidation float64, obstacles []float64) (got, legacy roomResult) {
	risk, structural := c.roomRisk(reference, invalidation)
	got = placeRoom(obstacles, reference, risk, structural, buy, c.MinimumTargetR, c.FallbackTargetR)
	legacy = placeRoom(obstacles, reference, structural, structural, buy, c.MinimumTargetR, c.FallbackTargetR)
	return got, legacy
}

func sameVerdict(a, b roomResult) bool { return len(a.Reasons) == len(b.Reasons) }

// A 5-pip structural stop on a 12-pip floor, the nearest obstacle 9 pips away: v11 saw 1.8R of room and published; execution risks 12 pips, so the real room is 0.75R.
func TestAFloorWidenedStopWithNineBlockedPipsIsRefusedForRoom(t *testing.T) {
	for _, buy := range []bool{true, false} {
		sign := 1.0
		if !buy {
			sign = -1
		}
		reference := 1.1000
		invalidation := reference - sign*5*fxPip
		obstacle := reference + sign*9*fxPip
		got, legacy := judge(fxConfig(), buy, reference, invalidation, []float64{obstacle})
		if len(legacy.Reasons) != 0 {
			t.Fatalf("buy=%v: v11 must have published this entry (9 pips >= 1R of 5 pips), got %+v", buy, legacy)
		}
		if !(len(got.Reasons) == 1 && got.Reasons[0] == refusedNoTargetRoom) {
			t.Fatalf("buy=%v: v12 must refuse no_target_room (9 pips < 1R of the 12-pip floor), got %+v", buy, got)
		}
		if !near(got.RoomR, 0.75) || !near(got.RoomStructuralR, 1.8) {
			t.Fatalf("buy=%v: room_r %.4f want 0.75 and room_r_structural %.4f want 1.8", buy, got.RoomR, got.RoomStructuralR)
		}
	}
}

// With a 13-pip structural stop (above the floor) nothing is widened, so v12 is v11.
func TestAStructuralStopAboveTheFloorIsUnchanged(t *testing.T) {
	reference, invalidation := 1.1000, 1.1000-13*fxPip
	for _, obstacle := range []float64{reference + 14*fxPip, reference + 12.9*fxPip, reference + 40*fxPip} {
		got, legacy := judge(fxConfig(), true, reference, invalidation, []float64{obstacle})
		if got.Target != legacy.Target || got.Label != legacy.Label || !near(got.RoomR, legacy.RoomR) || !near(got.RoomStructuralR, got.RoomR) || !sameVerdict(got, legacy) {
			t.Fatalf("obstacle %.5f: v12 %+v differs from v11 %+v", obstacle, got, legacy)
		}
	}
}

// Gold: the structural stop is above its floor, and an instrument with no budget has no floor at all: both are judged exactly as before.
func TestGoldWithoutWideningAndNoBudgetAreByteIdentical(t *testing.T) {
	reference, invalidation := 4300.0, 4300.0-60*xauPip
	for _, c := range []Config{xauConfig(), {MinimumTargetR: 1.0, FallbackTargetR: 2.0}} {
		for _, obstacles := range [][]float64{nil, {reference + 70*xauPip}, {reference + 59*xauPip}} {
			got, legacy := judge(c, true, reference, invalidation, obstacles)
			if got.Target != legacy.Target || got.Label != legacy.Label || got.RoomR != legacy.RoomR || got.RoomStructuralR != got.RoomR || !sameVerdict(got, legacy) {
				t.Fatalf("budget %+v obstacles %v: v12 %+v differs from v11 %+v", c.StopBudget, obstacles, got, legacy)
			}
		}
	}
	if risk, structural := xauConfig().roomRisk(reference, invalidation); risk != structural {
		t.Fatalf("effective risk %v must equal the structural risk %v when nothing is widened", risk, structural)
	}
}

// With nothing in the way the target is FallbackTargetR x the EFFECTIVE risk, and the structural ratio is published next to it.
func TestTheFallbackTargetIsTwiceTheEffectiveRisk(t *testing.T) {
	for _, buy := range []bool{true, false} {
		sign := 1.0
		if !buy {
			sign = -1
		}
		reference := 1.1000
		got, _ := judge(fxConfig(), buy, reference, reference-sign*5*fxPip, nil)
		want := reference + sign*2.0*12*fxPip
		if !near(got.Target, want) || got.Label != fixedRewardRisk {
			t.Fatalf("buy=%v: target %.6f label %q, want %.6f %q", buy, got.Target, got.Label, want, fixedRewardRisk)
		}
		if !near(got.RoomR, 2.0) || !near(got.RoomStructuralR, 2.0*12/5) {
			t.Fatalf("buy=%v: room_r %.4f want 2.0, room_r_structural %.4f want %.4f", buy, got.RoomR, got.RoomStructuralR, 2.0*12/5)
		}
		if len(got.Reasons) != 0 {
			t.Fatalf("buy=%v: a fallback target is never refused for room, got %+v", buy, got)
		}
	}
}
