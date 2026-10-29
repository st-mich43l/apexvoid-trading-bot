package shockretest

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "shock_retest.quality.v1"

// qualityContext are the facts of the surrounding market the score reads besides the setup itself.
type qualityContext struct {
	price      float64 // latest M5 close
	localTrend string  // the M5 structure: "up", "down" or "range"
	choch      bool
	htfAligned bool
}

// score adds the Shock Retest dimensions to the model m, each in [0, 1], averaged without weights. A stop beyond the leg's origin and a target with
// room are validity rules (the setup is refused otherwise), not scores.
//
//	shock_force      the leg in ATR measured before the shock: Ramp(d; 3, 8). A bigger shock is a stronger adjustment to news.
//	retracement      how deep the pullback went: Peak(r; 0.4, 0.5, 0.618, 0.786). The textbook band scores 1; a shallow or a nearly total pullback less.
//	rejection        the rejecting candle's lower wick ratio, Ramp(w; 0.2, 0.6).
//	entry_location   how far the latest close has left the proximal edge, in ATR: Fall(d; 0.25, 1.5).
//	structure        the direction of the market, graded (quality.StructureScore).
//	room             reward:risk to the shock extreme, 1R scoring 0 and 3R scoring 1.
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	m.Score("shock_force", quality.Ramp(setup.LegATR, 3, 8))
	m.Score("retracement", quality.Peak(setup.Retracement, 0.4, RetracementNear, RetracementFar, RetracementDead))
	m.Score("rejection", quality.Ramp(setup.RejectionWick, 0.2, 0.6))
	m.Score("entry_location", quality.LocationScore(qc.price, reference, setup.M5ATR))
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))
	room, _ := quality.GeometryRoom(reference, setup.Stop, setup.Target, true)
	m.Score("room", room)
}
