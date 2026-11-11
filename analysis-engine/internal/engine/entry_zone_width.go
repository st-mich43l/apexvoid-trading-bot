package engine

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// RefusedEntryZoneBelowMinimumWidth is the refusal reason counted as strategy_refused{strategy, reason} when an opportunity's entry zone is thinner than the
// instrument's minimum (stop_envelope.min_entry_zone_width_pips).
const RefusedEntryZoneBelowMinimumWidth = "entry_zone_below_minimum_width"

// EntryZoneBelowMinimumWidth reports whether a non-scalping candidate's published entry zone is narrower than minimumPips of the instrument (0 = no minimum).
// The width is the zone the strategy published, measured in the instrument's own pip (XAU: 0.10 price units, so 30 pips is 3.00); the comparison is exact to
// a thousandth of a pip, so 29.9 pips is refused and 30.0 accepted. A scalp (the M1 scalp stop-envelope kind) may enter at one price and is exempt: no
// intraday strategy is reclassified as a scalp to escape the rule, because the exemption is the strategy's own declared kind.
func EntryZoneBelowMinimumWidth(c opportunity.Candidate, geometry market.Geometry, minimumPips float64) bool {
	if !(minimumPips > 0) || !(geometry.PipSize > 0) {
		return false
	}
	if strategyStopEnvelope[c.Strategy] == stopEnvelopeM1Scalp {
		return false
	}
	widthPips := (c.Entry.High - c.Entry.Low) / geometry.PipSize
	if math.IsNaN(widthPips) {
		return true
	}
	return math.Round(widthPips*1000)/1000 < minimumPips
}
