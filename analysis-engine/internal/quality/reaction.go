package quality

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// RejectionScore scores the reaction candles of a confirmed rejection: half the refusal wick of the touch bar, Ramp(wick; 0.2, 0.6),
// half the close strength of the confirmation bar, Ramp(close; 0.5, 0.9). Indices outside the bars are missing evidence (0). A
// reaction confirmed on its own touch bar is one candle, and its wick and its close are two facts of that one candle, scored once
// inside this single dimension.
func RejectionScore(bars []market.Candle, touchIndex, confirmIndex int, buy bool) float64 {
	if touchIndex < 0 || touchIndex >= len(bars) || confirmIndex < 0 || confirmIndex >= len(bars) {
		return 0
	}
	wick := RejectionWick(bars[touchIndex], buy)
	closeStrength := CloseStrength(bars[confirmIndex], buy)
	return 0.5*Ramp(wick, 0.2, 0.6) + 0.5*Ramp(closeStrength, 0.5, 0.9)
}

// LocationScore is entry timing: how far the latest close already is from the proximal edge of the entry band, in ATR,
// Fall(d; 0.25, 1.5). Price that has run from the edge is a late entry, not a better one. Without a volatility there is no
// measurement (0).
func LocationScore(price, reference, atr float64) float64 {
	if !(atr > 0) || !finite(price) || !finite(reference) {
		return 0
	}
	return Fall(math.Abs(price-reference)/atr, 0.25, 1.5)
}

// StructureScore grades the direction of the market for the trade, as a graded context and not as an added bonus, so a confirmed
// reversal is not outranked merely because another setup is higher-timeframe aligned:
//
//	with the local structure, higher timeframe aligned / not   1.0 / 0.7
//	against it with a change of character in the trade's way   0.9 / 0.7
//	a range                                                    0.6 / 0.5
//	against it with no reversal evidence                       0.3 / 0.1
func StructureScore(buy bool, localTrend string, choch, htfAligned bool) float64 {
	with := buy && localTrend == "up" || !buy && localTrend == "down"
	against := buy && localTrend == "down" || !buy && localTrend == "up"
	pick := func(aligned, plain float64) float64 {
		if htfAligned {
			return aligned
		}
		return plain
	}
	switch {
	case with:
		return pick(1.0, 0.7)
	case against && choch:
		return pick(0.9, 0.7)
	case against:
		return pick(0.3, 0.1)
	default:
		return pick(0.6, 0.5)
	}
}

// GeometryRoom is Room measured from an entry reference to a target and an invalidation. It also returns the technical reward:risk
// (0 when the risk is not positive) for publication as a measurement.
func GeometryRoom(reference, invalidation, target float64, structural bool) (score, rewardRisk float64) {
	risk := math.Abs(reference - invalidation)
	reward := math.Abs(target - reference)
	if risk > 0 && finite(reward) {
		rewardRisk = reward / risk
	}
	return Room(reward, risk, structural), rewardRisk
}
