package shockretest

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Target kind: the shock extreme is the thesis' objective.
const TargetShockExtreme = "shock_extreme"

// Setup is one fully confirmed Shock Retest episode with its technical geometry. Every timestamp is a candle OPEN time in Unix seconds.
type Setup struct {
	Direction market.Direction

	// The shock leg: from the first candle's open to the extreme, completed at ShockTime (the last candle that made or extended it).
	OriginTime, ShockTime int64
	Origin, Extreme       float64
	LegATR                float64 // the leg in ATR measured BEFORE the shock
	RetestTime            int64   // the first candle inside the retracement band
	ConfirmedAt           int64
	ConfirmationAge       int

	EntryLow, EntryHigh float64
	Stop, Target        float64
	M5ATR               float64
	RiskPips            float64
	RewardPips          float64
	TechnicalRR         float64

	// Measured facts.
	WickRejection bool
	Engulfing     bool
	RejectionWick float64
	Retracement   float64 // how deep the pullback went into the leg, 0 at the extreme and 1 at the origin
}

// Rejection records why a real episode did not become a setup.
type Rejection struct {
	Reason    string
	Direction market.Direction
	At        int64
}

// Analysis is the complete outcome of one evaluation.
type Analysis struct {
	EvaluatedAt int64
	Shocks      int
	Setups      []Setup
	Rejections  []Rejection
}

// Rejection reasons.
const (
	ReasonInvalidCandles          = "invalid_candles"
	ReasonInsufficientM5History   = "insufficient_m5_history"
	ReasonNoRetest                = "no_retest"
	ReasonRetestTooDeep           = "retest_too_deep"
	ReasonRetestNotRejected       = "retest_not_rejected"
	ReasonCounterShock            = "counter_shock"
	ReasonInvalidatedAfterConfirm = "invalidated_after_confirmation"
	ReasonPriceThroughEntry       = "price_through_entry"
	ReasonInvalidStopGeometry     = "invalid_stop_geometry"
	ReasonNoTargetRoom            = "no_target_room"
	ReasonTargetAlreadyReached    = "target_already_reached"
	ReasonRiskExceedsEnvelope     = "risk_exceeds_execution_envelope"
	ReasonConfluenceBelowFloor    = "confluence_below_floor"
	ReasonATRUnavailable          = "atr_unavailable"
)
