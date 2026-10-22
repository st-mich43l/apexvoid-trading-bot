package crt

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "crt.quality.v1"

// qualityContext are the facts of the surrounding market the score reads besides the setup itself.
type qualityContext struct {
	price      float64 // latest M5 close
	localTrend string  // the M5 structure: "up", "down" or "range"
	choch      bool
	htfAligned bool
}

// score adds the CRT dimensions to the model m, each in [0, 1], each from a different event of the sequence, averaged without
// weights. Invalidation beyond the manipulation extreme is a validity rule of the setup (it is refused otherwise), not a score.
//
//	range_validity   the H1 reference range in H1 ATR: Peak(r; 0.6, 1.2, 3.5, 7). A sliver proves nothing; a very wide range is a trend.
//	sweep_depth      how far the manipulation went past the swept edge, in ATR: Peak(d; 0.05, 0.2, 1.0, 2.5). A wick that barely
//	                 pokes is not a raid of resting orders, and a very deep one is a breakout.
//	reclaim          the first close back inside the range: half Ramp(depth in ATR; 0.05, 0.6), half Fall(bars after the sweep; 0, 12).
//	structure_shift  the later M5 break of the counter-trend swing: Ramp(its displacement in ATR; 0.3, 1.2) when it is a later candle
//	                 than the reclaim, half of that when it is the reclaim candle itself (one candle is not two confirmations); 0
//	                 without a shift. Its candle is credited here and nowhere else.
//	range_position   where in the reference range the entry sits for the thesis (buy low, sell high): Fall(p; 0.25, 0.7).
//	entry_location   how far the latest close has left the proximal edge, in ATR: Fall(d; 0.25, 1.5).
//	structure        the direction of the market, graded (quality.StructureScore).
//	room             reward:risk to the structural target, 1R scoring 0 and 3R scoring 1.
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	m.Score("range_validity", quality.Peak(setup.Anchor.RangeATR, 0.6, 1.2, 3.5, 7))
	m.Score("sweep_depth", quality.Peak(setup.Sweep.DepthATR, 0.05, 0.2, 1.0, 2.5))
	m.Score("reclaim", 0.5*quality.Ramp(setup.Reclaim.DepthATR, 0.05, 0.6)+0.5*quality.Fall(float64(setup.Reclaim.BarsAfterSweep), 0, 12))
	shift := 0.0
	if setup.Shift != nil {
		shift = quality.Ramp(setup.Shift.DisplacementATR, 0.3, 1.2)
		if setup.Shift.BarsAfterReclaim < 1 {
			shift *= 0.5
		}
	}
	m.Score("structure_shift", shift)

	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	position := math.NaN()
	if width := setup.Anchor.Width(); width > 0 {
		position = (reference - setup.Anchor.Low) / width
		if !buy {
			position = 1 - position
		}
	}
	m.Score("range_position", quality.Fall(position, 0.25, 0.7))
	m.Score("entry_location", quality.LocationScore(qc.price, reference, setup.M5ATR))
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))
	room, _ := quality.GeometryRoom(reference, setup.Stop, setup.Target, true)
	m.Score("room", room)
}
