package ifvg

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "ifvg.quality.v2"

// qualityContext are the facts of the surrounding market the score reads besides the setup itself.
type qualityContext struct {
	bars       []market.Candle // closed M5 bars up to the confirmation
	price      float64         // latest close
	localTrend string          // the frame's structure: "up", "down" or "range"
	choch      bool            // a change of character in the trade's direction on a bar after the inversion candle, up to the rejection (setupCHoCH)
	htfAligned bool
	// effectiveRisk is the distance from the proximal entry edge to the stop the trade is actually exposed to: the structural stop, or the instrument's
	// floor when that is tighter and execution widens it (StopBudget.EffectiveRisk). Zero when it is unknown.
	effectiveRisk float64
}

// score adds the iFVG dimensions to the model m, each in [0, 1], each from a different candle or fact, averaged without weights:
//
//	gap_imbalance       the original imbalance (the candle that left the gap): half Peak(width in ATR; 0.15, 0.4, 1.5, 3.5), half
//	                    Ramp(the origin candle's body in ATR; 0.5, 1.5). A sliver is noise and an enormous gap is a regime break.
//	inversion_strength  the closing candle that inverted it, three facts, a third each (v2): how far it closed through the gap,
//	                    Peak(close beyond the far bound in ATR; 0.05, 0.5, 2.0, 3.5); how large the move was against the volatility,
//	                    Peak(body in ATR; 0.3, 0.8, 2.0, 3.5); and how directional the candle was, half Ramp(body share of range; 0.5, 0.9)
//	                    and half Ramp(close location; 0.5, 0.9). Decisive, clean and not an exhausted spike: the extension bounds (2.0
//	                    and 3.5 ATR) are the same for the penetration and for the body. This candle is credited here and nowhere else.
//	inversion_timing    bars from the gap to its inversion: Peak(n; 0, 2, 12, 36). An inversion inside the same move is not a
//	                    separate event, and one a long time later is a different market.
//	retest_freshness    touches of the inverted gap including this one, and the retest's age: half Fall(n; 1, 4), half
//	                    Peak(bars since the inversion; 0, 2, 12, 36).
//	retest_rejection    the rejection candle only (v2): half Ramp(close location; 0.5, 0.9), half the refusal it confirmed with, the
//	                    better of its lower wick, Ramp(wick; 0.2, 0.6), and its directional body, Ramp(body share of range; 0.5, 0.9).
//	                    The detector accepts a wick OR a body-led rejection, so the score credits whichever confirmed it.
//	zone_integrity      one minus the deepest close back into the gap since the inversion, as a share of its width (measured).
//	entry_location      how far the latest close has run from the proximal edge of the entry band in the trade's favour, in ATR:
//	                    Fall(d; 0.25, 1.5); price inside the band is a better fill and scores in full (v2).
//	structure           the direction of the market, graded (quality.StructureScore). A change of character counts only when it broke
//	                    on a bar after the inversion candle (that candle is the inversion's own event) up to the rejection (v2).
//	room                reward over the risk the trade is actually exposed to, 1R scoring 0 and 3R scoring 1 (v2): the structural stop
//	                    distance, or the instrument's floor when execution widens a tighter stop.
func (s *Strategy) score(m *quality.Model, setup Setup, qc qualityContext) {
	m.Score("gap_imbalance", 0.5*quality.Peak(setup.Gap.WidthATR, 0.15, 0.4, 1.5, 3.5)+0.5*quality.Ramp(setup.Gap.OriginBodyATR, 0.5, 1.5))
	inv := setup.Inversion
	candle := 0.5*quality.Ramp(inv.BodyRatio, 0.5, 0.9) + 0.5*quality.Ramp(inv.CloseStrength, 0.5, 0.9)
	m.Score("inversion_strength", (quality.Peak(inv.CloseBeyondATR, 0.05, 0.5, 2.0, 3.5)+quality.Peak(inv.DisplacementATR, 0.3, 0.8, 2.0, 3.5)+candle)/3)
	m.Score("inversion_timing", quality.Peak(float64(setup.Inversion.BarsFromGap), 0, 2, 12, 36))
	m.Score("retest_freshness", 0.5*quality.Fall(float64(setup.Retest.Touches), 1, 4)+0.5*quality.Peak(float64(setup.Retest.BarsAfterInversion), 0, 2, 12, 36))
	refusal := math.Max(quality.Ramp(setup.Retest.WickRatio, 0.2, 0.6), quality.Ramp(setup.Retest.BodyRatio, 0.5, 0.9))
	m.Score("retest_rejection", 0.5*quality.Ramp(setup.Retest.CloseStrength, 0.5, 0.9)+0.5*refusal)
	m.Score("zone_integrity", setup.ZoneIntegrity)

	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	strategyutil.ScoreEntryLocation(m, buy, qc.price, reference, setup.ATR)
	m.Score("structure", quality.StructureScore(buy, qc.localTrend, qc.choch, qc.htfAligned))

	// Room is scored against the risk execution takes; the structural reward:risk is still published (target_room_r).
	g := quality.MeasureRoom(buy, reference, setup.Stop, setup.Target, qc.effectiveRisk, true)
	m.Score("room", g.Score)
	if g.EffectiveRisk > 0 {
		m.Measure("room_r_effective", g.EffectiveRoomR)
	}
	strategyutil.PublishRiskGeometry(m, g, s.cfg.PipSize)
}
