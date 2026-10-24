package strategyutil

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// ReactionExtremeEvidence is published on a candidate whose invalidation was set by the extreme of the reaction's own bars rather than by the
// structure: the thesis is then invalidated beyond a price the confirmation has already traded.
const ReactionExtremeEvidence = "invalidation_beyond_reaction_extreme"

// ReactionAnchoredInvalidation is the invalidation of a confirmed reaction: beyond the far edge of the structure AND beyond the extreme price
// traded by the bars from the touch to the confirmation, by the buffer.
//
//	BUY : min(structLow,  lowest low  of bars[touch..confirm]) - buffer
//	SELL: max(structHigh, highest high of bars[touch..confirm]) + buffer
//
// A sweep or strong reclaim is confirmed by a wick that pierces the band, so a stop at "structure minus buffer" can sit inside the very bars that
// confirmed the setup and be taken by a price that has already traded. anchored reports that the reaction's extreme, not the structure, set the
// price (it lies strictly beyond the structure's edge). A missing confirmation, an index outside the bars, a touch after its confirmation or a
// non-finite extreme is missing evidence: the structural price is returned unchanged and anchored is false. The function reads only bars up to the
// confirmation, so it cannot depend on a later bar.
func ReactionAnchoredInvalidation(bars []market.Candle, conf *LegacyConfirmation, direction market.Direction, structLow, structHigh, buffer float64) (price float64, anchored bool) {
	structural := structLow - buffer
	if direction == market.Sell {
		structural = structHigh + buffer
	}
	if conf == nil || conf.TouchIndex < 0 || conf.ConfirmationIndex >= len(bars) || conf.TouchIndex > conf.ConfirmationIndex {
		return structural, false
	}
	if direction == market.Sell {
		extreme := math.Inf(-1)
		for _, b := range bars[conf.TouchIndex : conf.ConfirmationIndex+1] {
			extreme = math.Max(extreme, b.High)
		}
		if math.IsInf(extreme, 0) || math.IsNaN(extreme) || !(extreme > structHigh) {
			return structural, false
		}
		return extreme + buffer, true
	}
	extreme := math.Inf(1)
	for _, b := range bars[conf.TouchIndex : conf.ConfirmationIndex+1] {
		extreme = math.Min(extreme, b.Low)
	}
	if math.IsInf(extreme, 0) || math.IsNaN(extreme) || !(extreme < structLow) {
		return structural, false
	}
	return extreme - buffer, true
}
