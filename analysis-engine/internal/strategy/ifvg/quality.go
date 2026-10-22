package ifvg

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "ifvg.quality.v1"

// qualityContext are the facts of the surrounding market the score reads besides the setup itself.
type qualityContext struct {
	bars       []market.Candle // closed M5 bars up to the confirmation
	price      float64         // latest close
	localTrend string          // the frame's structure: "up", "down" or "range"
	choch      bool
	htfAligned bool
}

// score adds the iFVG dimensions to the model m, each in [0, 1], each from a different candle or fact, averaged without weights:
//
//	gap_imbalance       the original imbalance (the candle that left the gap): half Peak(width in ATR; 0.15, 0.4, 1.5, 3.5), half
//	                    Ramp(the origin candle's body in ATR; 0.5, 1.5). A sliver is noise and an enormous gap is a regime break.
//	inversion_strength  the closing candle that inverted it: half Ramp(close beyond the far bound in ATR; 0.05, 0.5), half
//	                    Peak(its body in ATR; 0.3, 0.8, 2.0, 3.5). Decisive but not an exhausted spike. This candle is credited here
//	                    and nowhere else.
//	inversion_timing    bars from the gap to its inversion: Peak(n; 0, 2, 12, 36). An inversion inside the same move is not a
//	                    separate event, and one a long time later is a different market.
//	retest_freshness    touches of the inverted gap including this one, and the retest's age: half Fall(n; 1, 4), half
//	                    Peak(bars since the inversion; 0, 2, 12, 36).
//	retest_rejection    the retest candle only: half Ramp(close strength; 0.5, 0.9), half Ramp(refusal wick; 0.2, 0.6).
//	zone_integrity      one minus the deepest close back into the gap since the inversion, as a share of its width (measured).
//	entry_location      how far the latest close has left the proximal edge, in ATR: Fall(d; 0.25, 1.5).
//	structure           the direction of the market, graded (quality.StructureScore).
//	room                reward:risk to the opposing structure, 1R scoring 0 and 3R scoring 1.
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	m.Score("gap_imbalance", 0.5*quality.Peak(setup.Gap.WidthATR, 0.15, 0.4, 1.5, 3.5)+0.5*quality.Ramp(setup.Gap.OriginBodyATR, 0.5, 1.5))
	m.Score("inversion_strength", 0.5*quality.Ramp(setup.Inversion.CloseBeyondATR, 0.05, 0.5)+0.5*quality.Peak(setup.Inversion.DisplacementATR, 0.3, 0.8, 2.0, 3.5))
	m.Score("inversion_timing", quality.Peak(float64(setup.Inversion.BarsFromGap), 0, 2, 12, 36))
	m.Score("retest_freshness", 0.5*quality.Fall(float64(setup.Retest.Touches), 1, 4)+0.5*quality.Peak(float64(setup.Retest.BarsAfterInversion), 0, 2, 12, 36))
	m.Score("retest_rejection", 0.5*quality.Ramp(setup.Retest.CloseStrength, 0.5, 0.9)+0.5*quality.Ramp(setup.Retest.WickRatio, 0.2, 0.6))
	m.Score("zone_integrity", setup.ZoneIntegrity)

	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	m.Score("entry_location", quality.LocationScore(qc.price, reference, setup.ATR))
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))
	room, _ := quality.GeometryRoom(reference, setup.Stop, setup.Target, true)
	m.Score("room", room)
}
