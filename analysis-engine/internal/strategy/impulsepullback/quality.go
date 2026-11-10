package impulsepullback

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "impulse_pullback.quality.v3"

// qualityInputs are the facts of one qualified impulse pullback the score reads.
type qualityInputs struct {
	displacementATR    float64 // impulse length over the structural ATR
	minDisplacementATR float64 // the strategy's own minimum, which the setup already met
	bodyDominance      float64
	minBodyDominance   float64
	pullbackRatio      float64 // mean pullback body over mean impulse body
	maxPullbackRatio   float64 // the strategy's own corrective ceiling
	referenceTouches   int
	buy                bool
	price, reference   float64
	atr                float64
	rewardPips, stop   float64 // the selected ladder rung and the stop
	// corridorRoomPips is the usable room of the scalp corridor (the active M5 range) from the worst planned entry to the corridor edge ahead. It is the
	// structural objective; the selected rung is not.
	corridorRoomPips float64
}

// scoreQuality is Impulse Pullback's technical quality: a decisive impulse, a shallow corrective pullback into a structure that has
// been respected, and the room to run. Six dimensions in [0, 1], averaged without weights. The thresholds the setup already had to
// meet are the lower ends of the ranges, so a setup that barely qualified scores low rather than full.
//
//	impulse_strength    displacement over its minimum: Peak(x; 0.8, 1.2, 2.5, 4). Decisive, but a spike beyond 2.5 times is exhausted.
//	body_dominance      the share of the leg that is real body: Ramp(b; minimum, 0.9).
//	pullback_quality    how corrective the pullback is: Fall(pullback body / impulse body; 0.1, ceiling).
//	reference_quality   independent touches of the structure the pullback rests on: Peak(n; 1, 2, 4, 8).
//	entry_location      how far the latest close has run from the proximal edge against the trade, in ATR: Fall(d; 0.25, 1.5). v3: direction-aware; price deeper in
//	                    the zone is not a chase. entry_chase_atr and entry_favourable_depth_atr are published.
//	room                the corridor's usable room from the worst planned entry as reward:risk against the stop, 1R scoring 0 and 3R scoring 1. The selected
//	                    ladder rung is a reward:risk the strategy chose, not a structural objective, and is not scored as room.
func scoreQuality(in qualityInputs) *quality.Model {
	m := quality.New(QualityModel)
	ratio := math.NaN()
	if in.minDisplacementATR > 0 {
		ratio = in.displacementATR / in.minDisplacementATR
	}
	m.Score("impulse_strength", quality.Peak(ratio, 0.8, 1.2, 2.5, 4))
	m.Score("body_dominance", quality.Ramp(in.bodyDominance, in.minBodyDominance, 0.9))
	m.Score("pullback_quality", quality.Fall(in.pullbackRatio, 0.1, in.maxPullbackRatio))
	m.Score("reference_quality", quality.Peak(float64(in.referenceTouches), 1, 2, 4, 8))
	strategyutil.ScoreEntryLocation(m, in.buy, in.price, in.reference, in.atr)
	m.Score("room", quality.Room(in.corridorRoomPips, in.stop, true))
	m.Measure("impulse_displacement_atr", in.displacementATR)
	m.Measure("body_dominance", in.bodyDominance)
	m.Measure("pullback_body_ratio", in.pullbackRatio)
	m.Measure("reference_touches", float64(in.referenceTouches))
	m.Measure("scalp_reward_risk", in.rewardPips/in.stop)
	m.Measure("corridor_room_pips", in.corridorRoomPips)
	m.Measure("corridor_room_r", in.corridorRoomPips/in.stop)
	return m
}
