package strategyutil

// Room of a scalp: the finite distance, in pips, from the worst executable entry to the nearest thing that can stop the trade, measured once and
// causally. It replaces the unlimited room (math.Inf(1)) Range Scalp and Impulse Retrace used to hand to ScalpTarget.
//
// A scalp's room is the smaller of
//
//	the strategy's own objective   a price its thesis names and the strategy owns (the far edge of the range; the projection of the impulse), and
//	the nearest opposing barrier   a live, side-correct wall of the engine's canonical zone book, normalised by internal/barrier exactly as it is
//	                               published to the Algo Bot, less the instrument's barrier buffer.
//
// The barrier is the nearest wall AHEAD of the entry on the opposing side: for a BUY a supply wall whose upper edge is above the entry, for a SELL a
// demand wall whose lower edge is below it. A wall that contains the entry has no room before it (room <= 0) and is never skipped because it is "the
// same wall" the entry sits on. Only zones that existed at the decision (created at or before the last closed bar) and, when the strategy names a
// cutoff, were not born of the candidate's own structure (origin before the cutoff) count. Nothing here reads a bar after the decision.
//
// Prices are price units; pips are converted with the instrument's own pip size.

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/barrier"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// Limiters name what bounded a scalp's room.
const (
	ScalpRoomLimitedByObjective = "objective"
	ScalpRoomLimitedByBarrier   = "barrier"
)

// ScalpRoomParams are the per-instrument facts the room is measured with. They are injected by the engine for the scalp lane
// (execution.policy.execution_zone_max_width_* and auto_algo.actionability.target_room.barrier_buffer_atr, instrument overrides included): the same
// leaves the Algo Bot's execution-time barrier check reads, so Go and the execution policy judge one set of walls.
type ScalpRoomParams struct {
	PipSize             float64
	ATR                 float64 // the M5 ATR the barrier buffer is a multiple of
	BarrierBufferATR    float64
	BarrierMaxWidthATR  float64
	BarrierMaxWidthPips float64
}

// ScalpRoom is the measured room of one scalp.
type ScalpRoom struct {
	// Pips is the available room from the worst executable entry: min(ObjectivePips, BarrierPips). It is finite; zero or negative means there is none.
	Pips float64
	// ObjectivePips is the distance from the worst entry to the strategy's own objective.
	ObjectivePips float64
	// BarrierPips is the room to the nearest opposing barrier less the buffer; NaN when no barrier lies ahead of the entry.
	BarrierPips float64
	// RawBarrierPips is the same distance before the buffer; NaN when there is no barrier.
	RawBarrierPips float64
	Limiter        string
	// Barrier is the nearest opposing barrier ahead of the entry (nil when none).
	Barrier *barrier.Barrier
}

// HasBarrier reports whether a barrier lies ahead of the entry.
func (r ScalpRoom) HasBarrier() bool { return r.Barrier != nil }

// MeasureScalpRoom measures the room of a scalp in direction from worst, the entry price that is worst for the trade (the zone's far edge: the high of
// a BUY band, the low of a SELL band). objective is the price the strategy's own thesis names ahead of the entry; a value that is not ahead of the entry, or
// not finite, leaves no room. originBefore, when non-zero, drops zones that began at or after it (the candidate's own structure).
func MeasureScalpRoom(ctx *analysiscontext.MarketContext, direction market.Direction, worst, objective float64, originBefore int64, p ScalpRoomParams) ScalpRoom {
	room := ScalpRoom{BarrierPips: math.NaN(), RawBarrierPips: math.NaN()}
	if !(p.PipSize > 0) || math.IsNaN(worst) || math.IsInf(worst, 0) || math.IsNaN(objective) || math.IsInf(objective, 0) {
		return room
	}
	ahead := objective - worst
	if direction == market.Sell {
		ahead = worst - objective
	}
	room.ObjectivePips = ahead / p.PipSize
	room.Pips, room.Limiter = room.ObjectivePips, ScalpRoomLimitedByObjective
	b, raw, ok := nearestOpposingBarrier(ctx, direction, worst, originBefore, p)
	if !ok {
		return room
	}
	buffer := math.Max(0, p.BarrierBufferATR) * math.Max(0, p.ATR)
	room.Barrier = &b
	room.RawBarrierPips = raw / p.PipSize
	room.BarrierPips = (raw - buffer) / p.PipSize
	if room.BarrierPips < room.Pips {
		room.Pips, room.Limiter = room.BarrierPips, ScalpRoomLimitedByBarrier
	}
	return room
}

// ScalpRoomTimeframes are the timeframes the barrier book is built from, the Algo Bot's own scope (barrier.DefaultTimeframes).
var scalpRoomTimeframes = []market.Timeframe{market.M5, market.M15, market.H1}

// liveBarrierState is the lifecycle a zone must be in to count as a standing wall (the zone book's liveZoneState).
func liveBarrierState(s zone.State) bool {
	return s == zone.StateFresh || s == zone.StateTouched || s == zone.StatePartiallyMitigated
}

// scalpBarriers is the canonical barrier book as the Algo Bot receives it, built from the zones that existed at the decision. originBefore, when
// non-zero, drops zones that began at or after it (the candidate's own structure).
func scalpBarriers(ctx *analysiscontext.MarketContext, originBefore int64, p ScalpRoomParams) []barrier.Barrier {
	if ctx == nil || !(p.BarrierMaxWidthATR > 0) || !(p.BarrierMaxWidthPips > 0) {
		return nil
	}
	asOf := int64(0)
	if m5 := ctx.Timeframes[market.M5]; m5 != nil && len(m5.Candles) > 0 {
		// The decision is the close of the last M5 bar: a zone created after it did not exist yet.
		asOf = m5.Candles[len(m5.Candles)-1].Time + 300
	}
	var zones []barrier.Zone
	for _, tf := range scalpRoomTimeframes {
		frame := ctx.Timeframes[tf]
		if frame == nil {
			continue
		}
		for _, z := range frame.Zones.Zones {
			var side barrier.Side
			switch z.Kind {
			case zone.KindSupply:
				side = barrier.Sell
			case zone.KindDemand:
				side = barrier.Buy
			default:
				continue
			}
			if asOf > 0 && z.CreatedAt > asOf || originBefore > 0 && z.OriginTime >= originBefore {
				continue
			}
			zones = append(zones, barrier.Zone{
				Timeframe: string(tf), Side: side, Low: float64(z.Low), High: float64(z.High), ATR: frame.Zones.ATR,
				Score: z.Strength, Touches: z.TouchCount, Live: liveBarrierState(z.State),
				ID: z.ID, State: z.State.String(), OriginTime: z.OriginTime, CreatedAt: z.CreatedAt, LastTouchedAt: z.LastTouchedAt,
			})
		}
	}
	return barrier.Build(zones, barrier.Config{
		Timeframes: barrier.DefaultTimeframes, PipSize: p.PipSize, MaxWidthATR: p.BarrierMaxWidthATR, MaxWidthPips: p.BarrierMaxWidthPips,
	})
}

// nearestOpposingBarrier returns the opposing barrier whose near edge is closest ahead of worst, and the raw distance (price units) to that edge. The near
// edge is the barrier's Low for a BUY (a wall above) and its High for a SELL (a wall below). A barrier the entry sits inside has a near edge behind the
// entry: its distance is zero or negative, and it is the nearest because nothing can be nearer than a wall the entry is in.
func nearestOpposingBarrier(ctx *analysiscontext.MarketContext, direction market.Direction, worst float64, originBefore int64, p ScalpRoomParams) (barrier.Barrier, float64, bool) {
	var zero barrier.Barrier
	wanted, best, bestDistance := barrier.Sell, zero, math.Inf(1)
	if direction == market.Sell {
		wanted = barrier.Buy
	}
	for _, b := range scalpBarriers(ctx, originBefore, p) {
		if b.Side != wanted {
			continue
		}
		var distance float64
		if direction == market.Buy {
			if !(b.High > worst) {
				continue // behind the entry: already passed
			}
			distance = b.Low - worst
		} else {
			if !(b.Low < worst) {
				continue
			}
			distance = worst - b.High
		}
		if distance < bestDistance {
			best, bestDistance = b, distance
		}
	}
	if math.IsInf(bestDistance, 1) {
		return zero, 0, false
	}
	return best, bestDistance, true
}

// SupportingBarrier is a live wall on the trade's own side (a demand wall for a BUY, a supply wall for a SELL) that overlaps [low, high], the price span a
// probe or a retrace traded through. It is structural context a strategy may score when it genuinely exists; it never limits room.
func SupportingBarrier(ctx *analysiscontext.MarketContext, direction market.Direction, low, high float64, originBefore int64, p ScalpRoomParams) (barrier.Barrier, bool) {
	wanted := barrier.Buy
	if direction == market.Sell {
		wanted = barrier.Sell
	}
	var best barrier.Barrier
	found := false
	for _, b := range scalpBarriers(ctx, originBefore, p) {
		if b.Side != wanted || !(b.Low <= high && b.High >= low) {
			continue
		}
		if !found || b.Touches > best.Touches {
			best, found = b, true
		}
	}
	return best, found
}

// ScalpRoomATR is the M5 ATR the barrier buffer is a multiple of: the zone book's own (the value the execution policy reads), else the strategy's.
func ScalpRoomATR(ctx *analysiscontext.MarketContext, fallback float64) float64 {
	if ctx != nil {
		if m5 := ctx.Timeframes[market.M5]; m5 != nil && m5.Zones.ATR > 0 {
			return m5.Zones.ATR
		}
	}
	return fallback
}
