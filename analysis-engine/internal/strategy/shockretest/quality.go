package shockretest

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "shock_retest.quality.v2"

// qualityContext are the facts of the surrounding market the score reads besides the setup itself.
type qualityContext struct {
	price      float64 // latest M5 close
	localTrend string  // the M5 structure: "up", "down" or "range"
	choch      bool
	htfAligned bool
	// room is the measured room of the entry (strategyutil.MeasureQualityRoom): the effective risk, and the room limited by the nearest valid opposing barrier.
	room    strategyutil.QualityRoom
	pipSize float64
}

// score adds the Shock Retest dimensions to the model m, each in [0, 1], averaged without weights. A stop beyond the leg's origin and a target with
// room are validity rules (the setup is refused otherwise), not scores.
//
//	shock_force      the leg in ATR measured before the shock: Ramp(d; 3, 8). A bigger shock is a stronger adjustment to news.
//	retracement      how deep the pullback went: Peak(r; 0.4, 0.5, 0.618, 0.786). The textbook band scores 1; a shallow or a nearly total pullback less.
//	rejection        the rejecting candle's lower wick ratio, Ramp(w; 0.2, 0.6).
//	entry_location   how far the latest close has run from the proximal edge against the trade, in ATR: Fall(d; 0.25, 1.5); deeper in the band is not a chase (v2).
//	structure        the direction of the market, graded (quality.StructureScore).
//	room             structural room over the effective risk, 1R scoring 0 and 3R scoring 1 (v2): the shock extreme is observed structure, the 1.272 extension is a projection and scores room only up to a
//	                 barrier actually ahead; the risk is the structural stop or the execution floor when wider.
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	m.Score("shock_force", quality.Ramp(setup.LegATR, 3, 8))
	m.Score("retracement", quality.Peak(setup.Retracement, 0.4, RetracementNear, RetracementFar, RetracementDead))
	m.Score("rejection", quality.Ramp(setup.RejectionWick, 0.2, 0.6))
	strategyutil.ScoreEntryLocation(m, buy, qc.price, reference, setup.M5ATR)
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))
	strategyutil.ScoreRoom(m, qc.room, qc.pipSize)
}

// qualityRoom measures the room of the entry from the same executable reference the risk is measured from. The target is observed structure only when TargetShockExtreme;
// anything else (a projection, a measured move, a handle) is a target hypothesis and earns structural room only up to a barrier actually ahead of the entry.
func (s *Strategy) qualityRoom(ctx *analysiscontext.MarketContext, setup Setup, reference float64) strategyutil.QualityRoom {
	return strategyutil.MeasureQualityRoom(ctx, setup.Direction == market.Buy, reference, setup.Stop, setup.Target, setup.TargetKind == TargetShockExtreme, s.budget,
		s.barrier.RoomParams(s.cfg.PipSize, strategyutil.ScalpRoomATR(ctx, setup.M5ATR)), 0)
}
