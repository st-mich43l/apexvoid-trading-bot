package roundnumber

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "round_number.quality.v2"

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

// score adds the Round Number dimensions to the model m, each in [0, 1], each from a different event of the sequence, averaged without weights.
// Invalidation beyond the manipulation extreme and a target with room are validity rules (the setup is refused otherwise), not scores.
//
//	force            how decisively the structure moved: the structure-shift candle (stop run) or the break candle (accepted break) in ATR, Ramp(d; 0.3, 1.5).
//	rejection        the wick that refused the level: the sweep candle's (stop run) or the retest candle's (accepted break) wick ratio, Ramp(w; 0.2, 0.6).
//	sweep_depth      stop run only: how far past the handle the manipulation went, in ATR, Peak(d; 0.05, 0.3, 1.5, 4); an accepted break scores the
//	                 depth of its retest into the band as 0.5 (neutral).
//	entry_location   how far the latest close has run from the proximal edge against the trade, in ATR: Fall(d; 0.25, 1.5); deeper in the band is not a chase (v2).
//	structure        the direction of the market, graded (quality.StructureScore).
//	room             structural room over the effective risk, 1R scoring 0 and 3R scoring 1 (v2): an opposing liquidity pool or the opposite edge is observed structure, a range
//	                 midpoint or a measured move is a hypothesis; the room is limited by the nearest valid opposing barrier.
//	major_handle     1 on a major handle (a multiple of major_step), 0 on an ordinary one.
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	m.Score("force", quality.Ramp(setup.ForceATR, 0.3, 1.5))
	m.Score("rejection", quality.Ramp(setup.RejectionWick, 0.2, 0.6))
	depth := 0.5
	if setup.Kind == KindSweepReclaim {
		depth = quality.Peak(setup.SweepDepthATR, 0.05, 0.3, 1.5, 4)
	}
	m.Score("sweep_depth", depth)
	strategyutil.ScoreEntryLocation(m, buy, qc.price, reference, setup.M5ATR)
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))
	strategyutil.ScoreRoom(m, qc.room, qc.pipSize)
	major := 0.0
	if setup.Major {
		major = 1
	}
	m.Score("major_handle", major)
}

// qualityRoom measures the room of the entry from the same executable reference the risk is measured from. The target is observed structure only when TargetLiquidity;
// anything else (a projection, a measured move, a handle) is a target hypothesis and earns structural room only up to a barrier actually ahead of the entry.
func (s *Strategy) qualityRoom(ctx *analysiscontext.MarketContext, setup Setup, reference float64) strategyutil.QualityRoom {
	return strategyutil.MeasureQualityRoom(ctx, setup.Direction == market.Buy, reference, setup.Stop, setup.Target, setup.TargetKind == TargetLiquidity, s.budget,
		s.barrier.RoomParams(s.cfg.PipSize, strategyutil.ScalpRoomATR(ctx, setup.M5ATR)), 0)
}
