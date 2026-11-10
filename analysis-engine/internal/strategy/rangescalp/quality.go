package rangescalp

import (
	"math"
	"time"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// score is Range Scalp's technical quality (range_scalp.quality.v2): five facts about one probe, each in [0, 1], averaged without weights (the overall is
// the mean of the score_* dimensions). No candle event is counted twice: the probe's depth, the location of the entry and the room are three different
// measurements.
//
//	range_integrity     how sideways the range is: Fall(efficiency; 0, maximum_efficiency_ratio). A drifting range is a trend with a box drawn around it.
//	edge_probe          the significance of the excursion into the edge band, in ATR: Peak(d; 0, 0.10, 0.60, 1.50). A scrape of the band is noise; a healthy
//	                    probe is a few tenths of an ATR; an excursion beyond 1.5 ATR is a breakout wearing a wick and scores 0 (depth is never rewarded indefinitely).
//	entry_location      where in the range the worst entry sits, as a fraction of the range from the edge it fades: Fall(p; 0.10, 0.50). Fading from the edge keeps
//	                    the whole range ahead; an entry at the middle has half of it.
//	room_reward_risk    the available room (the nearer of the far edge and the nearest valid opposing barrier, see strategyutil.MeasureScalpRoom) as reward:risk
//	                    against the stop: Ramp(room/stop; 1, 3). 1R of room scores 0 and 3R scores 1.
//	structural_support  a live wall of the trade's own side overlaps the probe's span: 1, otherwise 0 (no structural evidence). The dimension is on every
//	                    opportunity: the schema is fixed and the overall is always the mean of the same five dimensions.
func (s *Strategy) score(ctx *analysiscontext.MarketContext, sig signal, worst, stop, targetPips float64, room strategyutil.ScalpRoom) *quality.Model {
	cfg := s.cfg
	pip := cfg.PipSize
	m := quality.New(QualityModel)
	m.Score("range_integrity", quality.Fall(sig.Efficiency, 0, cfg.MaximumEfficiencyRatio))
	m.Score("edge_probe", quality.Peak(sig.ProbeDepthATR, 0, 0.10, 0.60, 1.50))
	position := math.NaN()
	if sig.Width > 0 {
		if sig.Direction == market.Sell {
			position = (sig.High - worst) / sig.Width
		} else {
			position = (worst - sig.Low) / sig.Width
		}
	}
	m.Score("entry_location", quality.Fall(position, 0.10, 0.50))
	m.Score("room_reward_risk", quality.Room(room.Pips, stop, true))
	low, high := sig.SignalLow, worst
	if sig.Direction == market.Sell {
		low, high = worst, sig.SignalHigh
	}
	wall, supported := strategyutil.SupportingBarrier(ctx, sig.Direction, low, high, 0, strategyutil.ScalpRoomParams{
		PipSize: pip, ATR: strategyutil.ScalpRoomATR(ctx, sig.ATR), BarrierBufferATR: cfg.BarrierBufferATR,
		BarrierMaxWidthATR: cfg.BarrierMaxWidthATR, BarrierMaxWidthPips: cfg.BarrierMaxWidthPips,
	})
	m.Score("structural_support", boolValue(supported))
	m.Measure("support_wall_present", boolValue(supported))
	m.Measure("support_wall_touches", float64(wall.Touches)) // zero when there is no wall
	m.Measure("range_width_atr", sig.Width/sig.ATR)
	m.Measure("efficiency_ratio", sig.Efficiency)
	m.Measure("probe_depth_atr", sig.ProbeDepthATR)
	m.Measure("entry_range_position", position)
	m.Measure("range_midpoint", (sig.High+sig.Low)/2)
	m.Measure("range_high", sig.High)
	m.Measure("range_low", sig.Low)
	m.Measure("available_room_pips", room.Pips)
	m.Measure("objective_room_pips", room.ObjectivePips)
	if room.HasBarrier() {
		m.Measure("barrier_room_pips", room.BarrierPips)
	}
	m.Measure("barrier_ahead", boolValue(room.HasBarrier()))
	m.Measure("room_limited_by_barrier", boolValue(room.Limiter == strategyutil.ScalpRoomLimitedByBarrier))
	m.Measure("room_reward_risk", room.Pips/stop)
	m.Measure("target_pips", targetPips)
	m.Measure("target_r", targetPips/stop)
	m.Measure("utc_hour", float64(time.Unix(sig.BarTime, 0).UTC().Hour()))
	return m
}

func boolValue(v bool) float64 {
	if v {
		return 1
	}
	return 0
}
