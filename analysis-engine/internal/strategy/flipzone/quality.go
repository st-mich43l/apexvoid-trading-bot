package flipzone

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "flip_zone.quality.v1"

// scoreQuality is Flip Zone's technical quality: a level that price broke and accepted, now retested from the new side. Seven
// dimensions in [0, 1], averaged without weights:
//
//	level_significance  independent touches of the level that flipped: Peak(n; 1, 3, 5, 9). A level that mattered, not one that was
//	                    worn out.
//	break_strength      the candle that broke it: body in ATR, Ramp(body; 0.5, 1.5). A weak poke through a level is not a flip.
//	retest_timing       bars from the break to the retest: Peak(n; 0, 1, 6, 30). The flip is only published once its role has been
//	                    accepted (a validity rule), so a quick retest is the normal case and a retest long after is another market.
//	rejection, entry_location, structure, room   see strategyutil.SharedReactionDimensions.
func scoreQuality(zone techniquezone.Zone, level techniquezone.Level, dec *strategyutil.TechniqueDecision, g strategyutil.QualityGeometry) opportunity.StrategyQuality {
	m := quality.New(QualityModel)
	bars := dec.Detector.Frame.Bars
	touchIndex := len(bars) - 1
	if dec.Confirmation != nil {
		touchIndex = dec.Confirmation.TouchIndex
	}

	m.Score("level_significance", quality.Peak(float64(level.Touches), 1, 3, 5, 9))
	m.Measure("level_touches", float64(level.Touches))

	breakBody := math.NaN()
	if zone.BreakIndex >= 0 && zone.BreakIndex < len(bars) && g.ATR > 0 {
		b := bars[zone.BreakIndex]
		breakBody = math.Abs(b.Close-b.Open) / g.ATR
	}
	m.Score("break_strength", quality.Ramp(breakBody, 0.5, 1.5))
	m.Measure("break_body_atr", breakBody)

	sinceBreak := math.NaN()
	if zone.BreakIndex >= 0 && touchIndex >= zone.BreakIndex {
		sinceBreak = float64(touchIndex - zone.BreakIndex)
	}
	m.Score("retest_timing", quality.Peak(sinceBreak, 0, 1, 6, 30))
	m.Measure("bars_break_to_retest", sinceBreak)

	// Freshness is a validity rule here, not a score: a flip is only published while its zone is unmitigated, so every published
	// flip is on its first retest.

	strategyutil.SharedReactionDimensions(m, dec, g)
	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}
