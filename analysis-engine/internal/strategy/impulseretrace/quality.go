package impulseretrace

import (
	"math"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// score is Impulse Retrace's technical quality (impulse_retrace.quality.v2): five facts about one impulse and its planned retrace, each in [0, 1], averaged
// without weights (the overall is the mean of the score_* dimensions). A pending retrace entry has no rejection candle yet and is not scored on one.
//
//	impulse_strength    the body over the ATR known BEFORE the bar, above the strategy's own minimum: Ramp(body/ATR; minimum, minimum + 2).
//	impulse_efficiency  the share of the bar that is body: Ramp(body/range; 0.4, 0.9). A long wick is price that was refused on the way.
//	retrace_depth       the planned retrace as a fraction of the bar's range back from its extreme: Peak(f; 0.20, 0.38, 0.62, 0.80). A shallow limit is rarely
//	                    filled, a deep one is a reversal; the 38 to 62 percent band is the healthy pullback of an impulse.
//	invalidation_quality the stop outside the noise but not absurdly wide, in M5 ATR: Peak(stop/ATR; 0.4, 0.8, 1.8, 3.0). The stop is beyond the impulse origin.
//	room_reward_risk    the STRUCTURAL room from the planned entry as reward:risk against the stop: Ramp(room/stop; 1, 3). Room is structural only when an
//	                    actual opposing barrier lies ahead (the room is bounded by it, or the space up to the projection was seen clear up to it). The
//	                    equal-leg projection is a technical target hypothesis, not observed structure: a room that rests on it alone scores 0 here (quality.Room
//	                    with structural=false) although it still makes the target eligible (strategyutil.MeasureScalpRoom). A strong impulse with little
//	                    structural room does not average up to an excellent score.
func (s *Strategy) score(bar market.Candle, atr, stop, targetPips, extremeRoomPips float64, room strategyutil.ScalpRoom) *quality.Model {
	cfg := s.cfg
	span := bar.High - bar.Low
	body := math.Abs(bar.Close - bar.Open)
	bodyATR := body / atr
	m := quality.New(QualityModel)
	m.Score("impulse_strength", quality.Ramp(bodyATR, cfg.ImpulseBodyATR, cfg.ImpulseBodyATR+2))
	m.Score("impulse_efficiency", quality.Ramp(body/span, 0.4, 0.9))
	m.Score("retrace_depth", quality.Peak(cfg.RetraceFraction, 0.20, 0.38, 0.62, 0.80))
	m.Score("invalidation_quality", quality.Peak(stop*cfg.PipSize/atr, 0.4, 0.8, 1.8, 3.0))
	structuralRoom, structural := room.StructuralPips(false) // the impulse's objective is a projection, never observed structure
	m.Score("room_reward_risk", quality.Room(structuralRoom, stop, structural))
	m.Measure("impulse_body_atr", bodyATR)
	m.Measure("impulse_body_dominance", body/span)
	m.Measure("retrace_fraction", cfg.RetraceFraction)
	m.Measure("stop_atr", stop*cfg.PipSize/atr)
	m.Measure("extreme_room_pips", extremeRoomPips)
	// Available room (eligibility) is the projected room bounded by any barrier; the structural room is what observation supports. They are published apart.
	m.Measure("available_room_pips", room.Pips)
	m.Measure("projected_room_pips", room.ObjectivePips)
	m.Measure("structural_room_basis", boolValue(structural))
	m.Measure("structural_room_pips", structuralRoom)
	m.Measure("barrier_ahead", boolValue(room.HasBarrier()))
	if room.HasBarrier() {
		m.Measure("barrier_room_pips", room.BarrierPips)
	}
	m.Measure("room_limited_by_barrier", boolValue(room.Limiter == strategyutil.ScalpRoomLimitedByBarrier))
	m.Measure("room_reward_risk", room.Pips/stop)
	m.Measure("structural_room_r", structuralRoom/stop)
	m.Measure("target_pips", targetPips)
	m.Measure("target_r", targetPips/stop)
	m.Measure("utc_hour", float64(time.Unix(bar.Time, 0).UTC().Hour()))
	return m
}

func boolValue(v bool) float64 {
	if v {
		return 1
	}
	return 0
}
