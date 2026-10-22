package trendline

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	technical "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "trendline.quality.v1"

// scoreQuality is Trendline's technical quality: a causally confirmed line, respected, and reclaimed at the latest bar. Eight
// dimensions in [0, 1], averaged without weights:
//
//	line_validation    independent validation touches after the two anchors: Peak(n; 0, 2, 4, 8). Two points make a line, a third
//	                   and fourth make it respected, and a line touched many times has been worn through.
//	line_integrity     violations: 1 - Ramp(2 * close violations + wick violations; 0, 4). A line price keeps closing through is
//	                   not defended.
//	line_significance  the line's span and slope: half Ramp(span in bars; 20, 100), half Fall(|slope| in ATR per bar; 0.05, 0.4). A
//	                   long, gentle line is structure; a short steep one is a momentum burst.
//	approach           the move into the line, in ATR, before it touched: Ramp(d; 0.3, 1.5). A reaction from a distance means price
//	                   went to the line; price hovering at it is not a reaction.
//	rejection, entry_location, structure, room   see strategyutil.SharedReactionDimensions.
func scoreQuality(line technical.Trendline, interaction technical.Interaction, dec *strategyutil.TechniqueDecision, g strategyutil.QualityGeometry) opportunity.StrategyQuality {
	m := quality.New(QualityModel)
	validations := float64(len(line.ValidationTouches))
	m.Score("line_validation", quality.Peak(validations, 0, 2, 4, 8))
	m.Measure("validation_touches", validations)

	violations := float64(2*line.CloseViolations + line.WickViolations)
	m.Score("line_integrity", 1-quality.Ramp(violations, 0, 4))
	m.Measure("line_violations_weighted", violations)

	span, slope := float64(line.SpanBars), math.Abs(line.SlopeATRPerBar)
	m.Score("line_significance", 0.5*quality.Ramp(span, 20, 100)+0.5*quality.Fall(slope, 0.05, 0.4))
	m.Measure("line_span_bars", span)
	m.Measure("line_slope_atr_per_bar", slope)

	m.Score("approach", quality.Ramp(interaction.PreTouchDistanceATR, 0.3, 1.5))
	m.Measure("approach_distance_atr", interaction.PreTouchDistanceATR)

	strategyutil.SharedReactionDimensions(m, dec, g)
	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}
