package strategyutil

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

// QualityGeometry is the entry geometry a quality model measures room and location from.
type QualityGeometry struct {
	// Reference is the proximal edge of the published entry band, Invalidation the structural stop, Target the nominal objective.
	Reference, Invalidation, Target float64
	ATR                             float64
	// Room is the measured room of the entry (MeasureQualityRoom): the risk the trade is exposed to, the structural stop's share of it, and the room ahead
	// limited by the nearest valid opposing barrier. A target that is a fixed reward:risk is not observed structure and earns no structural room.
	Room    QualityRoom
	PipSize float64
}

// QualityFunc scores one technique decision. A strategy that has its own thesis dimensions (a trendline, a flip) sets it on the
// decision; the zone family uses ZoneFamilyQuality.
type QualityFunc func(dec *TechniqueDecision, g QualityGeometry) opportunity.StrategyQuality

// SharedReactionDimensions adds the dimensions every confirmed rejection has, each from a different source: the reaction candles,
// the entry's chase from the proximal edge in the trade's direction, the direction of the market, and the structural room over the risk the trade is exposed to.
func SharedReactionDimensions(m *quality.Model, dec *TechniqueDecision, g QualityGeometry) {
	d := dec.Detector
	buy := dec.Direction == market.Buy
	rejection := 0.0
	if dec.Confirmation != nil {
		rejection = quality.RejectionScore(d.Frame.Bars, dec.Confirmation.TouchIndex, dec.Confirmation.ConfirmationIndex, buy)
	}
	m.Score("rejection", rejection)
	ScoreEntryLocation(m, buy, d.Price, g.Reference, g.ATR)
	m.Score("structure", quality.StructureScore(buy, d.Frame.Structure, d.RecentCHoCH(maxInt(1, d.Settings.ReactionLookbackBars)), d.HTFAligned()))
	ScoreRoom(m, g.Room, g.PipSize)
}

// ZoneFamilyQuality is the technical quality of a confirmed reaction off a supply/demand zone, an order block or a fair value gap.
// Seven dimensions, each in [0, 1], averaged without weights:
//
//	zone_departure   how forcefully price left the zone after it formed, in ATR, within eight bars and before the reaction:
//	                 Ramp(departure; 1, 3). A zone price left timidly had no one defending it.
//	zone_precision   the structure's width in ATR: Peak(width; 0.1, 0.4, 1.5, 3.5). A hairline is noise and a wide zone is a region.
//	zone_freshness   touches the zone has taken including this reaction: Fall(n; 1, 5). Each retest spends resting interest.
//	rejection, entry_location, structure, room   see SharedReactionDimensions.
//
// v2: entry_location is direction-aware and room is the structural room over the effective risk (ScoreEntryLocation, ScoreRoom).
func ZoneFamilyQuality(name string, dec *TechniqueDecision, g QualityGeometry) opportunity.StrategyQuality {
	m := quality.New(name + ".quality.v2")
	inst := dec.Instance
	buy := dec.Direction == market.Buy
	departure, width, touches := math.NaN(), math.NaN(), math.NaN()
	if inst != nil && g.ATR > 0 {
		lo, hi := inst.Low, inst.High
		if inst.StructuralHigh > inst.StructuralLow && inst.StructuralLow > 0 {
			lo, hi = inst.StructuralLow, inst.StructuralHigh
		}
		width = (hi - lo) / g.ATR
		touches = float64(inst.Touches)
		bars := dec.Detector.Frame.Bars
		end := len(bars)
		if dec.Confirmation != nil && dec.Confirmation.TouchIndex < end {
			end = dec.Confirmation.TouchIndex
		}
		start := maxInt(0, inst.OriginIndex)
		if stop := minInt(end, start+9); start < stop {
			best := 0.0
			for _, b := range bars[start:stop] {
				if buy {
					best = math.Max(best, b.High-hi)
				} else {
					best = math.Max(best, lo-b.Low)
				}
			}
			departure = best / g.ATR
		}
	}
	m.Score("zone_departure", quality.Ramp(departure, 1, 3))
	m.Score("zone_precision", quality.Peak(width, 0.1, 0.4, 1.5, 3.5))
	m.Score("zone_freshness", quality.Fall(touches, 1, 5))
	m.Measure("zone_departure_atr", departure)
	m.Measure("zone_width_atr", width)
	m.Measure("zone_touches", touches)
	SharedReactionDimensions(m, dec, g)
	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
