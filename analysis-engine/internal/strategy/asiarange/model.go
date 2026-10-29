package asiarange

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Setup kinds.
const (
	KindSweepReclaim = "sweep_reclaim"
	KindBreakRetest  = "break_retest"
)

// Target kinds.
const (
	TargetRangeMid     = "asia_range_mid"
	TargetOppositeEdge = "asia_range_opposite_edge"
	TargetLiquidity    = "nearest_opposing_liquidity"
	TargetMeasuredMove = "asia_range_measured_move"
)

// Edges.
const (
	EdgeHigh = "ASIA_H"
	EdgeLow  = "ASIA_L"
)

// Anchor is the finalized Asia range as of London open. Times are Unix seconds.
type Anchor struct {
	High, Low   float64
	AvailableAt int64
	// RangeATR is the range width in M15 ATR as of AvailableAt.
	RangeATR float64
}

func (a Anchor) Width() float64 { return a.High - a.Low }

// Setup is one fully confirmed Asia Range episode with its technical geometry. Every timestamp is a candle OPEN time in Unix seconds.
type Setup struct {
	Kind      string
	Direction market.Direction
	Edge      string
	Anchor    Anchor

	SweepTime, ReclaimTime, BreakTime, RetestTime int64
	ConfirmedAt                                   int64
	ConfirmationAge                               int

	EntryLow, EntryHigh float64
	Stop, Target        float64
	TargetKind          string
	M5ATR               float64
	RiskPips            float64
	RewardPips          float64
	TechnicalRR         float64

	// Measured facts.
	WickRejection bool
	Structural    bool
	Engulfing     bool
	SweepDepthATR float64
	ForceATR      float64 // the structure shift's displacement in M5 ATR (A) or the M15 break candle's in M15 ATR (B)
	BodyRatio     float64
	CloseStrength float64
	RejectionWick float64
	RetestBars    int
}

// Rejection records why a real episode did not become a setup.
type Rejection struct {
	Reason    string
	Kind      string
	Direction market.Direction
	Edge      string
	At        int64
}

// Analysis is the complete outcome of one evaluation.
type Analysis struct {
	EvaluatedAt int64
	Anchored    bool
	Setups      []Setup
	Rejections  []Rejection
}

// Rejection reasons beyond the shared level-episode vocabulary (strategyutil.LevelReason*).
const (
	ReasonInvalidCandles          = "invalid_candles"
	ReasonInsufficientM5History   = "insufficient_m5_history"
	ReasonInsufficientM15History  = "insufficient_m15_history"
	ReasonRangeTooNarrow          = "range_too_narrow"
	ReasonRangeTooWide            = "range_too_wide"
	ReasonEdgeLocked              = "edge_locked"
	ReasonInvalidatedAfterConfirm = "invalidated_after_confirmation"
	ReasonPriceThroughEntry       = "price_through_entry"
	ReasonInvalidStopGeometry     = "invalid_stop_geometry"
	ReasonNoTargetRoom            = "no_target_room"
	ReasonTargetAlreadyReached    = "target_already_reached"
	ReasonRiskExceedsEnvelope     = "risk_exceeds_execution_envelope"
	ReasonConfluenceBelowFloor    = "confluence_below_floor"
	ReasonSupersededBySibling     = "superseded_by_sibling"
)
