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
//	room_reward_risk    the available room from the planned entry (the horizon or the nearest valid opposing barrier, see strategyutil.MeasureScalpRoom) as
//	                    reward:risk against the stop: Ramp(room/stop; 1, 3). A strong impulse with little room does not average up to an excellent score.
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
	m.Score("room_reward_risk", quality.Room(room.Pips, stop, true))
	m.Measure("impulse_body_atr", bodyATR)
	m.Measure("impulse_body_dominance", body/span)
	m.Measure("retrace_fraction", cfg.RetraceFraction)
	m.Measure("stop_atr", stop*cfg.PipSize/atr)
	m.Measure("extreme_room_pips", extremeRoomPips)
	m.Measure("available_room_pips", room.Pips)
	m.Measure("objective_room_pips", room.ObjectivePips)
	if room.HasBarrier() {
		m.Measure("barrier_room_pips", room.BarrierPips)
	}
	boolean := 0.0
	if room.Limiter == strategyutil.ScalpRoomLimitedByBarrier {
		boolean = 1
	}
	m.Measure("room_limited_by_barrier", boolean)
	m.Measure("room_reward_risk", room.Pips/stop)
	m.Measure("target_pips", targetPips)
	m.Measure("target_r", targetPips/stop)
	m.Measure("utc_hour", float64(time.Unix(bar.Time, 0).UTC().Hour()))
	return m
}
