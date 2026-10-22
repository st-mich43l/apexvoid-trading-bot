package fadescalp

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "fade_scalp.quality.v1"

// qualityInputs are the facts of one confirmed sweep reaction the score reads: all of them exist when the reaction is confirmed.
type qualityInputs struct {
	bars         []market.Candle
	direction    market.Direction
	pool         techniquezone.Pool
	grab         *techniquezone.Grab
	confirmation *strategyutil.LegacyConfirmation
	facts        map[string]float64 // sweepFacts
	atr          float64
	price        float64 // latest close
	reference    float64 // proximal edge of the entry band
	invalidation float64
	objective    float64
}

// scoreQuality is Fade Scalp's technical quality: how clearly a stretched move swept a credible pool and failed to continue.
// Seven dimensions in [0, 1], averaged without weights. There is deliberately no trend-alignment dimension: the thesis is
// exhaustion, and a fade is as good against the move as with it.
//
//	pool_credibility     how credible the swept pool is as resting liquidity: a third each of Peak(touches; 1, 2, 4, 8) (two touches
//	                     make a pool, several an obvious one, many a worn one), Fall(band / ATR; 0.1, 0.6) (how equal the equal
//	                     highs or lows really are) and Peak(bars from its last swing to the sweep; 0, 3, 40, 120) (a pool just
//	                     formed has not rested, and an old one has been forgotten).
//	sweep                the raid itself: half Peak(penetration beyond the pool in ATR; 0.02, 0.1, 0.6, 1.5) (a deep penetration is a
//	                     breakout, a nothing is not a raid), half the grab grade (A 1.0, B 0.5).
//	extension            the move into the pool over the ten bars before the sweep, in ATR: Peak(run; 0.5, 1.5, 4, 8). Exhaustion
//	                     needs something to exhaust; a spike beyond that is a trend.
//	failed_continuation  how far back through the pool the confirmation closed, in ATR: Ramp(d; 0.05, 0.8).
//	rejection            the reaction candles (quality.RejectionScore), the candles after the sweep and not the sweep itself.
//	entry_location       how far the latest close has left the proximal edge, in ATR: Fall(d; 0.25, 1.5). A late entry after a big
//	                     displacement has used up the room.
//	room                 reward:risk to the structural objective (the local mean or opposing liquidity), 1R scoring 0, 3R scoring 1.
func scoreQuality(in qualityInputs) *quality.Model {
	m := quality.New(QualityModel)
	buy := in.direction == market.Buy

	tightness, age := math.NaN(), math.NaN()
	if in.atr > 0 {
		tightness = in.pool.Band / in.atr
	}
	if in.pool.LastIndex > 0 && in.pool.LastIndex <= in.grab.Index {
		age = float64(in.grab.Index - in.pool.LastIndex) // the pool's age when it was swept, not when it is read
	}
	m.Score("pool_credibility", (quality.Peak(float64(in.pool.Touches), 1, 2, 4, 8)+quality.Fall(tightness, 0.1, 0.6)+quality.Peak(age, 0, 3, 40, 120))/3)
	m.Measure("equal_level_touches", float64(in.pool.Touches))
	m.Measure("pool_band_atr", tightness)
	m.Measure("pool_age_bars", age)

	grade := 0.0
	switch in.grab.Grade {
	case "A":
		grade = 1
	case "B":
		grade = 0.5
	}
	penetration := in.facts["penetration_atr"]
	m.Score("sweep", 0.5*quality.Peak(penetration, 0.02, 0.1, 0.6, 1.5)+0.5*grade)
	m.Measure("sweep_penetration_atr", penetration)

	run := math.NaN()
	if v, ok := in.facts["run_into_sweep_atr"]; ok {
		run = v
	}
	m.Score("extension", quality.Peak(run, 0.5, 1.5, 4, 8))
	m.Measure("run_into_sweep_atr", run)

	back := math.NaN()
	if in.atr > 0 && in.confirmation.ConfirmationIndex >= 0 && in.confirmation.ConfirmationIndex < len(in.bars) {
		closePrice := in.bars[in.confirmation.ConfirmationIndex].Close
		back = (closePrice - in.pool.Level) / in.atr
		if !buy {
			back = -back
		}
	}
	m.Score("failed_continuation", quality.Ramp(back, 0.05, 0.8))
	m.Measure("close_back_through_pool_atr", back)

	m.Score("rejection", quality.RejectionScore(in.bars, in.confirmation.TouchIndex, in.confirmation.ConfirmationIndex, buy))
	m.Score("entry_location", quality.LocationScore(in.price, in.reference, in.atr))
	room, rewardRisk := quality.GeometryRoom(in.reference, in.invalidation, in.objective, true)
	m.Score("room", room)
	m.Measure("reward_risk", rewardRisk)
	return m
}
