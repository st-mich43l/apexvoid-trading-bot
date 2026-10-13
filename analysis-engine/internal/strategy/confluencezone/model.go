package confluencezone

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Rejection reasons. Every setup the detector considers and does not publish
// carries exactly one of them, in Analysis.Rejections.
const (
	ReasonNoTouch               = "no_touch_in_window"
	ReasonNoConfirmation        = "no_directional_confirmation"
	ReasonAcceptedThrough       = "price_accepted_through_region"
	ReasonFailedAfter           = "reaction_failed_after_confirmation"
	ReasonDegenerateStop        = "degenerate_stop"
	ReasonRiskEnvelope          = "stop_exceeds_execution_envelope"
	ReasonNoLiquidity           = "no_opposing_liquidity"
	ReasonInsufficientRoom      = "insufficient_target_room"
	ReasonRewardRisk            = "reward_risk_below_minimum"
	ReasonBlockedByStructure    = "target_blocked_by_opposing_structure"
	ReasonPriceThroughEntry     = "price_through_entry"
	ReasonEntryTooFar           = "entry_too_far"
	ReasonConfluenceFloor       = "confluence_floor"
	ReasonLevelOrEntryInvalid   = "level_or_entry_invalid"
	ReasonATRUnavailable        = "atr_unavailable"
	ReasonCandleSeries          = "invalid_candle_series"
	ReasonInsufficientHistory   = "insufficient_history"
	ReasonStructureNotAvailable = structuralconfluence.RejectNotYetAvailable
)

// Reaction is the measured rejection of a region by one closed candle.
type Reaction struct {
	// Pattern is a reaction pattern name from opportunity.ReactionPatterns:
	// sweep_reclaim (a wick through the far edge and a close back), wick_rejection
	// (a wick into the region and a close away from it) or strong_reclaim (a
	// displacement candle that closes through the region).
	Pattern string
	// The confirming candle's measured facts. Signed toward the trade: a BUY
	// measures the lower wick and the close's distance above the low; a SELL the
	// mirror image. All are side-independent numbers.
	WickRatio       float64
	CloseStrength   float64
	BodyRatio       float64
	RangeATR        float64
	SweepDepthATR   float64 // how far the wick went through the region's far edge
	ReclaimDepthATR float64 // how far inside (or beyond) the region the candle closed
	// Adverse is the extreme the reaction traded to against the trade, from the
	// touch candle to the confirming candle.
	Adverse float64
}

// Setup is a confirmed confluence reaction that passed every technical test.
type Setup struct {
	Direction market.Direction
	Region    structuralconfluence.Region
	Support   []structuralconfluence.Fact

	TouchIndex, ConfirmIndex int
	TouchTime, ConfirmTime   int64
	Reaction                 Reaction

	// Entry is the supported region, clipped to the instrument's proximal slice.
	EntryLow, EntryHigh float64
	// ProtectedFar is the far edge of the supporting structures; Stop is beyond
	// it and beyond the reaction's own adverse extreme.
	ProtectedFar, Stop float64
	Target             float64

	ATR           float64
	RiskPips      float64
	RewardRisk    float64
	TargetRoomATR float64

	// Measured quality components.
	IndependentFacts int
	DependencyCount  int // supporting facts minus independent groups
	OverlapWidthATR  float64
	OverlapWidthPips float64
	OverlapIntegrity float64
	SourceIntegrity  float64
	SourceAgeBars    float64
	Freshness        int // closed candles since the confirming one
	MaxSourceTouches int
	// RegionScore is the zone chain's score of the best supporting structure plus
	// the confluence bump for every additional independent source.
	RegionScore float64
}

// Resting is a region price is at that has not been confirmed: a technical
// observation, never an executable opportunity.
type Resting struct {
	Direction           market.Direction
	Region              structuralconfluence.Region
	EntryLow, EntryHigh float64
	Stop, Target        float64
	ATR                 float64
}

// Rejection is a considered setup that was not published, and why.
type Rejection struct {
	Direction   market.Direction
	Reason      string
	EpisodeID   string
	Low, High   float64
	TouchTime   int64
	ConfirmTime int64
	FactIDs     []string
}

// Analysis is the complete technical account of one evaluation.
type Analysis struct {
	// Facts are the structures the evaluation worked from.
	Facts []structuralconfluence.Fact
	// Regions are the supported regions as of the evaluated candle.
	Regions    []structuralconfluence.Region
	Setups     []Setup
	Resting    []Resting
	Rejections []Rejection
}

// Input is everything the pure detector reads.
type Input struct {
	Bars          []market.Candle
	ATR           float64
	PeriodSeconds int64
	// Facts are the structures as of now (the evaluated candle), for the regions
	// price is at and the opposing confluences.
	Facts []structuralconfluence.Fact
	// FactsAt, when set, yields the structures as they stood BEFORE the candle at
	// the given index opened: what a reaction touching that candle could rely on.
	// A structure the touch candle, or any later one, spent, filled or swept is
	// therefore still the structure it was. When nil, Facts are used for every touch.
	FactsAt func(index int) []structuralconfluence.Fact
	Pools   []techniquezone.Pool
	// Swings are the frame's confirmed swings: a liquidity pool is unswept only if
	// no candle since its latest member swing has traded beyond it.
	Swings []techniquezone.Swing
}

func (in Input) factsAt(index int) []structuralconfluence.Fact {
	if in.FactsAt != nil {
		return in.FactsAt(index)
	}
	return in.Facts
}
