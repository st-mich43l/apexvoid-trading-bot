package asiarange

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "asia_range.quality.v1"

// qualityContext are the facts of the surrounding market the score reads besides the setup itself.
type qualityContext struct {
	price      float64 // latest M5 close
	localTrend string  // the M5 structure: "up", "down" or "range"
	choch      bool
	htfAligned bool
}

// score adds the Asia Range dimensions to the model m, each in [0, 1], averaged without weights. A stop beyond the structural extreme and a target
// with room are validity rules (the setup is refused otherwise), not scores.
//
//	range_validity   the Asia range in M15 ATR: Peak(r; 1, 2, 4, 6). A sliver proves nothing; a very wide range is a trend, not a consolidation.
//	force            the structure shift (stop run) or the break candle (accepted break) in ATR, Ramp(d; 0.3, 1.5).
//	rejection        the sweep candle's (stop run) or the retest candle's (accepted break) wick ratio, Ramp(w; 0.2, 0.6).
//	sweep_depth      stop run only: how far past the edge the manipulation went, in ATR, Peak(d; 0.05, 0.3, 1.5, 4); an accepted break scores 0.5.
//	entry_location   how far the latest close has left the proximal edge, in ATR: Fall(d; 0.25, 1.5).
//	structure        the direction of the market, graded (quality.StructureScore).
//	room             reward:risk to the target, 1R scoring 0 and 3R scoring 1.
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	m.Score("range_validity", quality.Peak(setup.Anchor.RangeATR, 1, 2, 4, 6))
	m.Score("force", quality.Ramp(setup.ForceATR, 0.3, 1.5))
	m.Score("rejection", quality.Ramp(setup.RejectionWick, 0.2, 0.6))
	depth := 0.5
	if setup.Kind == KindSweepReclaim {
		depth = quality.Peak(setup.SweepDepthATR, 0.05, 0.3, 1.5, 4)
	}
	m.Score("sweep_depth", depth)
	m.Score("entry_location", quality.LocationScore(qc.price, reference, setup.M5ATR))
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))
	room, _ := quality.GeometryRoom(reference, setup.Stop, setup.Target, setup.TargetKind == TargetLiquidity || setup.TargetKind == TargetOppositeEdge)
	m.Score("room", room)
}
