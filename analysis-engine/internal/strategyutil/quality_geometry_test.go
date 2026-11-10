package strategyutil

import (
	"math"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// XAU: pip 0.1, execution stop floor 25 pips. BUY entry 4000, structural stop 6 pips behind it.
var goldBudget = StopBudget{MaxPips: 70, MinPips: 25, PipSize: 0.1}

func supplyWall(low, high float64) zone.Zone {
	return wall(zone.KindSupply, low, high, zone.StateFresh, decisionBar-3000, decisionBar-3000)
}

func TestTheEffectiveFloorComesFromTheExistingStopBudget(t *testing.T) {
	near(t, "floor", goldBudget.FloorPrice(), 2.5)
	if (StopBudget{}).FloorPrice() != 0 || (StopBudget{MinPips: 15}).FloorPrice() != 0 { // inactive without a pip size
		t.Error("no budget and no pip size mean no floor")
	}
	// The same floor StopBudget.EffectiveRisk applies: one source, not a second configuration.
	near(t, "EffectiveRisk", goldBudget.EffectiveRisk(4000, 3999.4), goldBudget.FloorPrice())
}

func measure(target float64, observed bool, zones ...zone.Zone) QualityRoom {
	c := contextWith(map[market.Timeframe][]zone.Zone{market.M15: zones})
	return MeasureQualityRoom(c, true, 4000, 3999.4, target, observed, goldBudget, xau, 0)
}

// C. Structural room.
func TestAnObservedObjectiveIsStructuralRoomOverTheEffectiveRisk(t *testing.T) {
	r := measure(4007.5, true) // 75 pips ahead, a real opposing objective
	if !r.Structural || !r.Geometry.Valid {
		t.Fatalf("%+v", r)
	}
	// 6 structural pips, 25 effective: 75 / 25 = 3R, not 75 / 6 = 12.5R.
	near(t, "effective room R", r.Geometry.EffectiveRoomR, 3)
	near(t, "structural room R", r.Geometry.StructuralRoomR, 12.5)
	near(t, "score", r.Geometry.Score, 1)
	near(t, "target reward R", r.TargetRewardR, 12.5)
}

func TestAFixedRewardRiskOrAProjectionIsNotStructuralRoom(t *testing.T) {
	// The same price, not observed (a fixed reward:risk, or an equal-leg or 1.272 projection), with no barrier anywhere: geometry is published, no structural credit.
	r := measure(4007.5, false)
	if r.Structural || r.Geometry.Score != 0 || !r.Geometry.Valid || math.Abs(r.Geometry.EffectiveRoomR-3) > 1e-9 {
		t.Errorf("%+v, want valid geometry, 3R, and no structural score", r)
	}
	// With an actual barrier ahead (and farther than the projection) the projected distance has been seen clear: structural.
	r = measure(4007.5, false, supplyWall(4020, 4021))
	if !r.Structural || r.Geometry.Score != 1 {
		t.Errorf("a barrier beyond the projection: %+v, want structural room", r)
	}
	// A barrier that is not ahead (behind a BUY) gives a projection nothing to stand on.
	behind := supplyWall(3990, 3995)
	if r = measure(4007.5, false, behind); r.Structural || r.Geometry.Score != 0 {
		t.Errorf("a wall behind the entry made a projection structural: %+v", r)
	}
}

func TestATargetBehindTheEntryHasNoRoom(t *testing.T) {
	for _, target := range []float64{3995, 4000} {
		r := measure(target, true)
		if r.Geometry.Valid || r.Geometry.Score != 0 || r.Geometry.EffectiveRoomR != 0 {
			t.Errorf("target %v: %+v, want no room", target, r.Geometry)
		}
	}
	// SELL mirror: a target above the entry.
	r := MeasureQualityRoom(contextWith(nil), false, 4000, 4000.6, 4005, true, goldBudget, xau, 0)
	if r.Geometry.Valid || r.Geometry.Score != 0 {
		t.Errorf("SELL target above the entry: %+v", r.Geometry)
	}
}

func TestAnImmediateOpposingBarrierLeavesNoRoom(t *testing.T) {
	inside := supplyWall(3999.5, 4002) // the entry sits inside the opposing wall: no same-wall exemption
	r := measure(4020, true, inside)
	if !r.Room.HasBarrier() || r.Geometry.Valid || r.Geometry.Score != 0 {
		t.Errorf("entry inside a wall: %+v, want a barrier and no usable room", r)
	}
	// A wall whose buffered room is gone: 4 raw pips less the 4.5-pip buffer.
	near1 := measure(4020, true, supplyWall(4000.4, 4001))
	if near1.Geometry.Valid || near1.Geometry.Score != 0 {
		t.Errorf("a wall 4 pips ahead with a 4.5-pip buffer: %+v, want no room", near1.Geometry)
	}
}

func TestABarrierNearerThanTheTargetLimitsTheRoom(t *testing.T) {
	r := measure(4020, true, supplyWall(4010, 4011)) // target 200 pips away, wall 100 raw pips ahead
	near(t, "target room pips", r.Room.ObjectivePips, 200)
	near(t, "barrier room pips (less the 4.5-pip buffer)", r.Room.BarrierPips, 95.5)
	near(t, "available room", r.Room.Pips, 95.5)
	if r.Room.Limiter != ScalpRoomLimitedByBarrier || !r.Structural {
		t.Errorf("%+v", r.Room)
	}
	near(t, "effective room R", r.Geometry.EffectiveRoomR, 95.5/25)
	// A farther barrier does not limit a nearer target.
	far := measure(4010, true, supplyWall(4030, 4031))
	near(t, "available room", far.Room.Pips, 100)
	// A stale (mitigated) wall and a wall created after the decision do not stand.
	stale := supplyWall(4010, 4011)
	stale.State = zone.StateMitigated
	future := wall(zone.KindSupply, 4010, 4011, zone.StateFresh, decisionBar+300, decisionBar+600)
	if got := measure(4020, true, stale, future); got.Room.HasBarrier() {
		t.Errorf("a stale or future wall limited the room: %+v", *got.Room.Barrier)
	}
}

func TestNoUsableRoom(t *testing.T) {
	// Observed target, but the geometry is degenerate: invalidation on the wrong side of the entry.
	r := MeasureQualityRoom(contextWith(nil), true, 4000, 4001, 4020, true, goldBudget, xau, 0)
	if r.Geometry.Valid || r.Geometry.Score != 0 {
		t.Errorf("%+v", r.Geometry)
	}
	// Without a pip size nothing can be placed.
	r = MeasureQualityRoom(contextWith(nil), true, 4000, 3999.4, 4020, true, goldBudget, ScalpRoomParams{}, 0)
	if r.Geometry.Valid || r.Geometry.Score != 0 {
		t.Errorf("no pip size: %+v", r.Geometry)
	}
}

// ScoreRoom publishes the geometry behind the score.
func TestScoreRoomPublishesTheRiskAndTheRoom(t *testing.T) {
	m := quality.New("test.quality.v2")
	r := measure(4007.5, true)
	ScoreRoom(m, r, 0.1)
	c := m.Components()
	for name, want := range map[string]float64{
		"score_room": 1, "structural_risk_pips": 6, "effective_risk_pips": 25, "effective_room_r": 3, "room_structural": 1,
		"barrier_ahead": 0, "room_limited_by_barrier": 0, "target_room_pips": 75, "available_room_pips": 75,
	} {
		if got, ok := c[name]; !ok || math.Abs(got-want) > 1e-9 {
			t.Errorf("%s = %v (present %v), want %v", name, got, ok, want)
		}
	}
	if _, ok := c["barrier_room_pips"]; ok {
		t.Error("no barrier, so no barrier distance")
	}
}

// D. Quality integrity of the shared dimensions.
func TestEntryLocationAndRoomPublishDiagnosticsAndStayBounded(t *testing.T) {
	for _, buy := range []bool{true, false} {
		for _, price := range []float64{3990, 3999, 4000, 4001, 4005, 4100, math.NaN()} {
			m := quality.New("test.quality.v2")
			ScoreEntryLocation(m, buy, price, 4000, 3)
			v := m.Components()["score_entry_location"]
			if v < 0 || v > 1 || math.IsNaN(v) {
				t.Errorf("buy=%v price %v: score %v", buy, price, v)
			}
			if !math.IsNaN(price) {
				chase, fav := m.Components()["entry_chase_atr"], m.Components()["entry_favourable_depth_atr"]
				if chase > 0 && fav > 0 {
					t.Errorf("price %v is both a chase and a favourable depth", price)
				}
			}
		}
	}
	// Missing ATR: scored 0, nothing published.
	m := quality.New("test.quality.v2")
	ScoreEntryLocation(m, true, 4000, 4000, 0)
	c := m.Components()
	if c["score_entry_location"] != 0 {
		t.Errorf("missing ATR scored %v", c["score_entry_location"])
	}
	if _, ok := c["entry_chase_atr"]; ok {
		t.Error("a chase was published without an ATR")
	}
}

func TestTheOverallStaysTheMeanOfTheScoredDimensions(t *testing.T) {
	m := quality.New("test.quality.v2")
	ScoreEntryLocation(m, true, 4001, 4000, 3)
	ScoreRoom(m, measure(4007.5, true), 0.1)
	m.Score("rejection", 0.4)
	sum, n := 0.0, 0
	for name, v := range m.Components() {
		if strings.HasPrefix(name, quality.Prefix) {
			sum += v
			n++
		}
	}
	if n != 3 || math.Abs(m.Overall()-sum/3) > 1e-12 {
		t.Errorf("overall %v, mean %v of %d dimensions", m.Overall(), sum/float64(n), n)
	}
	if m.Components()[quality.ModelVersionKey] != 2 {
		t.Errorf("model version %v, want 2", m.Components()[quality.ModelVersionKey])
	}
}

func TestParseBarrierPolicy(t *testing.T) {
	if b, err := ParseBarrierPolicy(map[string]any{}); err != nil || b != (BarrierPolicy{}) {
		t.Errorf("no keys: %+v, %v, want the zero policy (no barrier book)", b, err)
	}
	b, err := ParseBarrierPolicy(map[string]any{"barrier_buffer_atr": 0.15, "barrier_max_width_atr": 2.0, "barrier_max_width_pips": 100.0})
	if err != nil || b.BufferATR != 0.15 || b.MaxWidthATR != 2 || b.MaxWidthPips != 100 {
		t.Errorf("%+v, %v", b, err)
	}
	for name, params := range map[string]map[string]any{
		"partial":         {"barrier_buffer_atr": 0.15},
		"zero width":      {"barrier_buffer_atr": 0.15, "barrier_max_width_atr": 0.0, "barrier_max_width_pips": 100.0},
		"negative buffer": {"barrier_buffer_atr": -1.0, "barrier_max_width_atr": 2.0, "barrier_max_width_pips": 100.0},
	} {
		if _, err := ParseBarrierPolicy(params); err == nil {
			t.Errorf("%s: want an error", name)
		}
	}
	// Without a barrier book nothing limits a room and a projection earns no structural credit.
	r := MeasureQualityRoom(contextWith(map[market.Timeframe][]zone.Zone{market.M15: {supplyWall(4001, 4002)}}), true, 4000, 3999.4, 4007.5, false, goldBudget, BarrierPolicy{}.RoomParams(0.1, 3), 0)
	if r.Room.HasBarrier() || r.Structural {
		t.Errorf("no policy: %+v", r)
	}
}
