package crt

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// The typed CRT event chain. Every timestamp is a candle OPEN time in Unix
// seconds (the engine's convention); a candle's close is open plus its
// timeframe length. Nothing here carries a position in a candle slice across
// timeframes: the H1 anchor and every M5 event are related only by time.

// Anchor is the closed higher-timeframe candle that defines the range.
type Anchor struct {
	OpenTime  int64
	CloseTime int64
	High, Low float64
	// ATR is the Wilder ATR(14) of the H1 series as of this candle's close.
	ATR float64
	// RangeATR is (High-Low)/ATR.
	RangeATR float64
}

func (a Anchor) Width() float64 { return a.High - a.Low }
func (a Anchor) Mid() float64   { return (a.High + a.Low) / 2 }

// Sweep is the first M5 candle that took liquidity beyond the anchor edge by
// at least the sweep threshold, plus the deepest point of the manipulation.
type Sweep struct {
	// BarTime is the open time of the first piercing candle.
	BarTime int64
	// Threshold is the minimum penetration (price units) in force at BarTime.
	Threshold float64
	// ExtremePrice/ExtremeTime are the deepest manipulation point since the
	// anchor closed, through the confirming candle.
	ExtremePrice float64
	ExtremeTime  int64
	// Depth is how far past the swept edge the extreme went (price units).
	Depth    float64
	DepthATR float64
}

// Reclaim is the first completed M5 close back inside the original range.
type Reclaim struct {
	BarTime int64
	Close   float64
	// Depth is how far inside the swept edge the close is (price units).
	Depth    float64
	DepthATR float64
	// BarsAfterSweep counts M5 candles from the sweep candle (0 = same candle).
	BarsAfterSweep int
}

// StructureShift is the M5 market-structure shift that follows the reclaim: a
// completed candle closing beyond the previously confirmed counter-trend
// swing.
type StructureShift struct {
	// Level is the broken swing price; LevelTime its pivot candle's open time.
	Level     float64
	LevelTime int64
	// BarTime is the open time of the candle that closed beyond it.
	BarTime          int64
	Close            float64
	BodyRatio        float64
	DisplacementATR  float64
	CloseStrength    float64
	BarsAfterReclaim int
}

// Setup is one fully confirmed CRT episode with its technical geometry.
type Setup struct {
	Direction market.Direction
	Anchor    Anchor
	Sweep     Sweep
	Reclaim   Reclaim
	// Shift is nil only in the explicit sweep_reclaim baseline mode.
	Shift *StructureShift
	// ConfirmedAt is the open time of the confirming candle (the structure
	// shift, or the reclaim in the baseline mode); ConfirmationAge is how many
	// M5 candles before the evaluated candle it closed.
	ConfirmedAt     int64
	ConfirmationAge int
	// DoubleRaidResolved is set when the opposite extreme was also raided
	// earlier, closed back inside, and this later raid is the coherent one.
	DoubleRaidResolved bool

	EntryLow, EntryHigh float64
	Stop                float64
	Target              float64
	// M5ATR is the Wilder ATR(14) of the confirming candle.
	M5ATR float64

	RiskPips, RewardPips, TechnicalRR float64

	// Measured facts the confluence factors are scored from.
	WickRejection bool
}

// Rejection records why a real sweep episode did not become a setup. A
// rejection is a technical or execution-envelope fact, never an error.
type Rejection struct {
	Reason     string
	Direction  market.Direction
	AnchorTime int64
	SweepTime  int64
}

// Analysis is the complete outcome of one evaluation.
type Analysis struct {
	// EvaluatedAt is the close time of the evaluated M5 candle.
	EvaluatedAt int64
	Anchors     int
	Setups      []Setup
	Rejections  []Rejection
}

// Rejection reason codes. They are the stable vocabulary replay reports use.
const (
	ReasonInvalidCandles          = "invalid_candles"
	ReasonInsufficientH1History   = "insufficient_h1_history"
	ReasonInsufficientM5History   = "insufficient_m5_history"
	ReasonH1RangeBelowMinimum     = "h1_range_below_minimum"
	ReasonM5ATRUnavailable        = "m5_atr_unavailable"
	ReasonReclaimWindowExpired    = "reclaim_window_expired"
	ReasonReclaimOvershoot        = "reclaim_overshoot"
	ReasonReclaimLost             = "reclaim_lost"
	ReasonNoStructureReference    = "no_structure_reference"
	ReasonMSSWindowExpired        = "mss_window_expired"
	ReasonMSSQualityInsufficient  = "mss_quality_insufficient"
	ReasonBothExtremesRaided      = "both_extremes_raided_ambiguous"
	ReasonTargetAlreadyReached    = "target_already_reached"
	ReasonInvalidatedAfterConfirm = "invalidated_after_confirmation"
	ReasonPriceThroughEntry       = "price_through_entry"
	ReasonDegenerateEntryBand     = "degenerate_entry_band"
	ReasonInvalidStopGeometry     = "invalid_stop_geometry"
	ReasonInsufficientTargetRoom  = "insufficient_target_room"
	ReasonRewardRiskBelowMinimum  = "reward_risk_below_minimum"
	ReasonRiskExceedsEnvelope     = "risk_exceeds_execution_envelope"
	ReasonConfluenceBelowFloor    = "confluence_below_floor"
	ReasonSupersededByOpposite    = "superseded_by_opposite_confirmation"
)
