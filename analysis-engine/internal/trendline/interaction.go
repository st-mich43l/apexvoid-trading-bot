package trendline

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// InteractionState is the latest closed bar's classified relation to a
// V2 dynamic level — trendline_v2.py's evaluate_live_interaction's own
// state strings.
type InteractionState uint8

const (
	InteractionNoData InteractionState = iota
	InteractionFailedSupport
	InteractionFailedResistance
	InteractionAboveLine
	InteractionBelowLine
	InteractionApproachingSupport
	InteractionApproachingResistance
	InteractionReclaimedSupport
	InteractionReclaimedResistance
	InteractionTestingSupport
	InteractionTestingResistance
)

func (s InteractionState) String() string {
	switch s {
	case InteractionFailedSupport:
		return "FAILED_SUPPORT"
	case InteractionFailedResistance:
		return "FAILED_RESISTANCE"
	case InteractionAboveLine:
		return "ABOVE_LINE"
	case InteractionBelowLine:
		return "BELOW_LINE"
	case InteractionApproachingSupport:
		return "APPROACHING_SUPPORT"
	case InteractionApproachingResistance:
		return "APPROACHING_RESISTANCE"
	case InteractionReclaimedSupport:
		return "RECLAIMED_SUPPORT"
	case InteractionReclaimedResistance:
		return "RECLAIMED_RESISTANCE"
	case InteractionTestingSupport:
		return "TESTING_SUPPORT"
	case InteractionTestingResistance:
		return "TESTING_RESISTANCE"
	default:
		return "NO_DATA"
	}
}

// Interaction is evaluate_live_interaction's own TrendlineInteraction —
// a read-only classifier, never an entry signal (a reclaimed test still
// needs a new, separate trigger in whatever execution lifecycle
// consumes it — decided at S7, not here).
type Interaction struct {
	State InteractionState

	LinePrice market.Price
	BandLow   market.Price
	BandHigh  market.Price

	ApproachDirectionValid bool
	PreTouchDistanceATR    float64
	ApproachBars           int

	InteractionIndex *int
	InteractionTime  *int64

	RejectionReason string
}

// EvaluateInteraction ports evaluate_live_interaction exactly: the
// latest closed candle's relation to tl, classified in the same
// close-violation -> touch/no-touch -> approach-direction ->
// reclaimed/testing order Python's own function checks them in. atr is
// already a scalar (the caller's own current ATR reading) — unlike
// Build, this function is a live per-signal check with no series to
// reduce.
func EvaluateInteraction(candles []market.Candle, tl Trendline, atr float64, cfg Config) Interaction {
	if len(candles) == 0 {
		return Interaction{State: InteractionNoData, RejectionReason: "no_m5_data"}
	}
	atrValue := maxF(atr, eps)
	interactionBand := maxF(0, cfg.InteractionBandATR) * atrValue
	closeViolation := maxF(0, cfg.CloseViolationATR) * atrValue
	minimumApproach := maxF(0, cfg.ApproachMinDistanceATR) * atrValue

	index := len(candles) - 1
	linePrice := float64(ValueAt(tl, index))
	row := candles[index]
	low, high, close := float64(row.Low), float64(row.High), float64(row.Close)
	validSideClose := sideDistance(tl.Kind, close, linePrice)
	touch := low <= linePrice+interactionBand && high >= linePrice-interactionBand

	priorDistance := 0.0
	approachValid := false
	if index > 0 {
		priorLine := float64(ValueAt(tl, index-1))
		priorClose := float64(candles[index-1].Close)
		priorDistance = sideDistance(tl.Kind, priorClose, priorLine)
		approachValid = priorDistance >= minimumApproach
	}
	approachBars := 0
	if index > 0 {
		approachBars = 1
	}
	bandLow := market.Price(linePrice - interactionBand)
	bandHigh := market.Price(linePrice + interactionBand)
	t := candles[index].Time

	if validSideClose < -closeViolation {
		st := InteractionFailedSupport
		if tl.Kind == KindResistance {
			st = InteractionFailedResistance
		}
		idx := index
		return Interaction{
			State: st, LinePrice: market.Price(linePrice), BandLow: bandLow, BandHigh: bandHigh,
			ApproachDirectionValid: approachValid, PreTouchDistanceATR: priorDistance / atrValue,
			ApproachBars: approachBars, InteractionIndex: &idx, InteractionTime: &t,
			RejectionReason: "close_violation",
		}
	}

	if !touch {
		st := InteractionAboveLine
		if tl.Kind == KindResistance {
			st = InteractionBelowLine
		}
		if validSideClose >= 0 && validSideClose <= interactionBand*2.0 {
			st = InteractionApproachingSupport
			if tl.Kind == KindResistance {
				st = InteractionApproachingResistance
			}
		}
		return Interaction{
			State: st, LinePrice: market.Price(linePrice), BandLow: bandLow, BandHigh: bandHigh,
			ApproachDirectionValid: approachValid, PreTouchDistanceATR: priorDistance / atrValue,
			ApproachBars: approachBars,
		}
	}

	if !approachValid {
		st := InteractionFailedSupport
		if tl.Kind == KindResistance {
			st = InteractionFailedResistance
		}
		idx := index
		return Interaction{
			State: st, LinePrice: market.Price(linePrice), BandLow: bandLow, BandHigh: bandHigh,
			ApproachDirectionValid: false, PreTouchDistanceATR: priorDistance / atrValue,
			ApproachBars: approachBars, InteractionIndex: &idx, InteractionTime: &t,
			RejectionReason: "invalid_approach_direction",
		}
	}

	idx := index
	if validSideClose >= 0 {
		st := InteractionReclaimedSupport
		if tl.Kind == KindResistance {
			st = InteractionReclaimedResistance
		}
		return Interaction{
			State: st, LinePrice: market.Price(linePrice), BandLow: bandLow, BandHigh: bandHigh,
			ApproachDirectionValid: true, PreTouchDistanceATR: priorDistance / atrValue,
			ApproachBars: 1, InteractionIndex: &idx, InteractionTime: &t,
		}
	}
	st := InteractionTestingSupport
	if tl.Kind == KindResistance {
		st = InteractionTestingResistance
	}
	return Interaction{
		State: st, LinePrice: market.Price(linePrice), BandLow: bandLow, BandHigh: bandHigh,
		ApproachDirectionValid: true, PreTouchDistanceATR: priorDistance / atrValue,
		ApproachBars: 1, InteractionIndex: &idx, InteractionTime: &t,
		RejectionReason: "testing_without_reaction",
	}
}
