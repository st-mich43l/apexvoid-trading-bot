package strategyutil

// The geometry every quality model reads, measured once from the published entry: where price is against the entry in the trade's own direction, the risk
// the trade is exposed to (the structural stop, or the instrument's execution floor when that is wider) and the room ahead, limited by the nearest valid
// opposing barrier. A quality model keeps its own thesis dimensions; this file only measures geometry, and never scores a dimension a strategy did not ask for.
//
// It changes no trade: it reads the entry, the invalidation and the target a candidate already has.

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

// BarrierPolicy is the opposing-barrier policy a quality model limits its room with: the execution-zone width limits and the instrument's barrier buffer,
// the leaves the Algo Bot's target-room check reads (injected by the engine). The zero value means no barrier book: the room is then limited by the
// target alone and a target that is a projection earns no structural credit.
type BarrierPolicy struct{ BufferATR, MaxWidthATR, MaxWidthPips float64 }

// ParseBarrierPolicy reads barrier_buffer_atr, barrier_max_width_atr and barrier_max_width_pips; all three or none.
func ParseBarrierPolicy(params map[string]any) (BarrierPolicy, error) {
	var b BarrierPolicy
	present := 0
	for key, dst := range map[string]*float64{"barrier_buffer_atr": &b.BufferATR, "barrier_max_width_atr": &b.MaxWidthATR, "barrier_max_width_pips": &b.MaxWidthPips} {
		if _, ok := params[key]; !ok {
			continue
		}
		v, err := Float(params, key)
		if err != nil {
			return BarrierPolicy{}, err
		}
		*dst = v
		present++
	}
	switch {
	case present == 0:
		return BarrierPolicy{}, nil
	case present != 3 || b.BufferATR < 0 || !(b.MaxWidthATR > 0) || !(b.MaxWidthPips > 0):
		return BarrierPolicy{}, fmt.Errorf("the barrier policy needs barrier_buffer_atr >= 0 and positive barrier_max_width_atr and barrier_max_width_pips, all three")
	}
	return b, nil
}

// RoomParams are the parameters MeasureScalpRoom reads, for an instrument with this pip size and the M5 ATR atr.
func (b BarrierPolicy) RoomParams(pip, atr float64) ScalpRoomParams {
	return ScalpRoomParams{PipSize: pip, ATR: atr, BarrierBufferATR: b.BufferATR, BarrierMaxWidthATR: b.MaxWidthATR, BarrierMaxWidthPips: b.MaxWidthPips}
}

// FloorPrice is the instrument's execution stop floor in price units (0 when there is none or the budget is inactive). Execution widens a structural
// stop tighter than this to it; it is the same floor StopBudget.EffectiveRisk applies.
func (b StopBudget) FloorPrice() float64 {
	if b.Active() && b.MinPips > 0 {
		return b.MinPips * b.PipSize
	}
	return 0
}

// QualityRoom is the measured room of one entry for a quality model.
type QualityRoom struct {
	Geometry quality.RoomGeometry
	// Room is the room measured from the reference to the target and the nearest opposing barrier; its Pips is the distance a quality model scores.
	Room ScalpRoom
	// TargetStructural is whether the target is observed structure (a liquidity pool, an opposing zone, a swing, the far edge of a range), as opposed to a
	// fixed reward:risk or a projection.
	TargetStructural bool
	// Structural is whether the scored room rests on observed structure: an observed target, or a projected one with a barrier actually ahead.
	Structural bool
	// TargetRewardR is the target's own reward over the structural risk, before any barrier (the published room_reward_risk).
	TargetRewardR float64
}

// MeasureQualityRoom measures the room of an entry in direction buy. reference is the executable proximal edge (BUY: the band's high, SELL: its low),
// invalidation the structural stop and target the strategy's objective; targetObserved says whether that objective is observed structure. The room is the
// smaller of the target's distance and the nearest valid opposing barrier's (less the barrier buffer, once); a barrier the entry sits inside leaves none.
// A target that is not observed (fixed reward:risk, a projection) is structural only when an actual barrier lies ahead; otherwise it scores no room.
// budget supplies the execution stop floor; p the barrier policy and the pip size. originBefore, when non-zero, drops barriers born of the candidate itself.
func MeasureQualityRoom(ctx *analysiscontext.MarketContext, buy bool, reference, invalidation, target float64, targetObserved bool, budget StopBudget, p ScalpRoomParams, originBefore int64) QualityRoom {
	direction := market.Buy
	if !buy {
		direction = market.Sell
	}
	out := QualityRoom{TargetStructural: targetObserved}
	room := MeasureScalpRoom(ctx, direction, reference, target, originBefore, p)
	out.Room = room
	_, structural := room.StructuralPips(targetObserved)
	out.Structural = structural
	// The scored target: the reference moved ahead by the room that is left (a room of zero or less leaves a target that is not ahead).
	scored := reference
	if p.PipSize > 0 && !math.IsNaN(room.Pips) {
		sign := 1.0
		if !buy {
			sign = -1
		}
		scored = reference + sign*room.Pips*p.PipSize
	}
	out.Geometry = quality.MeasureRoom(buy, reference, invalidation, scored, budget.FloorPrice(), structural)
	out.TargetRewardR = quality.MeasureRoom(buy, reference, invalidation, target, 0, true).StructuralRoomR
	return out
}

// ScoreEntryLocation scores the "entry_location" dimension of model m in the trade's direction and publishes entry_chase_atr (how far price has run
// against the trade from the proximal edge) and entry_favourable_depth_atr (how far it is inside the band: diagnostic only, never rewarded).
func ScoreEntryLocation(m *quality.Model, buy bool, price, reference, atr float64) {
	m.Score("entry_location", quality.EntryLocation(buy, price, reference, atr))
	if chase, favourable, ok := quality.EntryChase(buy, price, reference, atr); ok {
		m.Measure("entry_chase_atr", chase)
		m.Measure("entry_favourable_depth_atr", favourable)
	}
}

// ScoreRoom scores the "room" dimension of model m from a measured room and publishes the geometry behind it: structural_risk_pips and effective_risk_pips
// (the risk the score is against), effective_room_r (the scored room over the effective risk), room_reward_risk (the target's own reward over the
// structural risk, the older measurement), available_room_pips, target_room_pips, room_structural (1 when the scored room is observed structure) and
// the barrier facts. pip converts price to pips; without one the pip figures are not published.
func ScoreRoom(m *quality.Model, r QualityRoom, pip float64) {
	g := r.Geometry
	m.Score("room", g.Score)
	m.Measure("room_structural", boolFloat(r.Structural && g.Valid))
	m.Measure("barrier_ahead", boolFloat(r.Room.HasBarrier()))
	m.Measure("room_limited_by_barrier", boolFloat(r.Room.Limiter == ScalpRoomLimitedByBarrier))
	if r.Room.HasBarrier() {
		m.Measure("barrier_room_pips", r.Room.BarrierPips)
	}
	m.Measure("room_reward_risk", r.TargetRewardR)
	PublishRiskGeometry(m, g, pip)
	m.Measure("target_room_pips", r.Room.ObjectivePips)
	m.Measure("available_room_pips", r.Room.Pips)
}

// PublishRiskGeometry publishes the risk the room was scored against: structural_risk_pips, effective_risk_pips (the structural stop, or the execution floor
// when that is wider) and effective_room_r (the room over the effective risk). Nothing is published for invalid geometry.
func PublishRiskGeometry(m *quality.Model, g quality.RoomGeometry, pip float64) {
	if !(g.StructuralRisk > 0) {
		return
	}
	m.Measure("effective_room_r", g.EffectiveRoomR)
	if pip > 0 {
		m.Measure("structural_risk_pips", g.StructuralRisk/pip)
		m.Measure("effective_risk_pips", g.EffectiveRisk/pip)
	}
}

func boolFloat(v bool) float64 {
	if v {
		return 1
	}
	return 0
}
