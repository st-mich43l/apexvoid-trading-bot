package breakretest

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "break_retest.quality.v2"

// qualityContext are the facts of the surrounding market the score reads besides the setup itself.
type qualityContext struct {
	price      float64 // latest close
	localTrend string  // the frame's structure: "up", "down" or "range"
	choch      bool
	htfAligned bool
	// room is the measured room of the entry: the structural risk and the effective one, and the room to the opposing structure limited by the nearest valid
	// opposing barrier (strategyutil.MeasureQualityRoom).
	room    strategyutil.QualityRoom
	pipSize float64
}

// score adds the Break & Retest dimensions to the model m, each in [0, 1], each from a different event of the sequence, averaged
// without weights:
//
//	reference_significance  independent touches of the broken level or line: Peak(n; 1, 3, 5, 9).
//	break_force             the breaking candle: half Peak(body in ATR; 0.4, 0.9, 2.5, 4), half Ramp(close beyond the level in ATR;
//	                        0.1, 0.8). Decisive, not an exhausted spike. This candle is credited here and nowhere else.
//	retest_quality          the return: half Peak(depth into the old level in ATR; 0, 0.05, 0.5, 1.2) (a retest that cuts back through
//	                        has failed), half Peak(bars since acceptance; 0, 2, 12, 40) (a late retest is another market).
//	retest_rejection        the retest candle only: half Ramp(close strength; 0.5, 0.9), half Ramp(refusal wick; 0.2, 0.6).
//	entry_location          how far the latest close has run from the proximal edge against the trade, in ATR: Fall(d; 0.25, 1.5); price deeper in the band is not
//	                        a chase (v2, direction-aware).
//	structure               the direction of the market, graded (quality.StructureScore).
//	room                    reward:risk to the opposing structure against the risk the trade is exposed to (the structural stop, or the execution floor when that is
//	                        wider), limited by the nearest valid opposing barrier, 1R scoring 0 and 3R scoring 1 (v2).
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	m.Score("reference_significance", quality.Peak(float64(setup.Reference.Touches), 1, 3, 5, 9))
	m.Score("break_force", 0.5*quality.Peak(setup.Break.DisplacementATR, 0.4, 0.9, 2.5, 4)+0.5*quality.Ramp(setup.Break.DistanceATR, 0.1, 0.8))
	// Closes held beyond the level are a validity rule of the setup (it needs exactly the configured number): published as a
	// measurement, not scored, because every published setup has the same count.
	m.Measure("break_accept_closes", float64(setup.Break.AcceptCloses))
	m.Score("retest_quality", 0.5*quality.Peak(setup.Retest.DepthATR, 0, 0.05, 0.5, 1.2)+0.5*quality.Peak(float64(setup.Retest.BarsAfterAcceptance), 0, 2, 12, 40))
	m.Score("retest_rejection", 0.5*quality.Ramp(setup.Retest.CloseStrength, 0.5, 0.9)+0.5*quality.Ramp(setup.Retest.WickRatio, 0.2, 0.6))
	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	strategyutil.ScoreEntryLocation(m, buy, qc.price, reference, setup.ATR)
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))
	strategyutil.ScoreRoom(m, qc.room, qc.pipSize)
}

// qualityRoom measures the room of the entry from the executable proximal edge. The target is the nearest opposing structure before the break (a confirmed
// pivot): observed structure, limited by the nearest valid opposing barrier, against the structural stop or the execution floor when that is wider.
func (s *Strategy) qualityRoom(ctx *analysiscontext.MarketContext, setup Setup) strategyutil.QualityRoom {
	reference := setup.EntryHigh
	if setup.Direction == market.Sell {
		reference = setup.EntryLow
	}
	return strategyutil.MeasureQualityRoom(ctx, setup.Direction == market.Buy, reference, setup.Stop, setup.Target, true,
		strategyutil.StopBudget{MinPips: s.cfg.ExecutionStopMinPips, PipSize: s.cfg.PipSize}, s.cfg.Barrier.RoomParams(s.cfg.PipSize, strategyutil.ScalpRoomATR(ctx, setup.ATR)), 0)
}
