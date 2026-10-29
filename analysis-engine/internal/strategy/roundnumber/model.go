package roundnumber

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// Setup kinds.
const (
	KindSweepReclaim = "sweep_reclaim"
	KindBreakRetest  = "break_retest"
)

// Target kinds.
const (
	TargetLiquidity  = "nearest_opposing_liquidity"
	TargetNextHandle = "next_round_handle"
)

// Setup is one fully confirmed Round Number episode with its technical geometry. Every timestamp is a candle OPEN time in Unix seconds.
type Setup struct {
	Kind      string
	Direction market.Direction
	Handle    float64
	Major     bool
	Day       int64

	// The sequence: sweep/reclaim for a stop run, break/retest for an accepted break.
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
	Structural    bool // the shift (stop run) or the break (accepted break) went through the most recent confirmed M5 swing
	Engulfing     bool
	SweepDepthATR float64
	ForceATR      float64 // the structure shift's (A) or the break candle's (B) displacement in ATR
	BodyRatio     float64
	CloseStrength float64
	RejectionWick float64 // the rejecting candle's wick ratio
	RetestBars    int
}

// Rejection records why a real episode did not become a setup.
type Rejection struct {
	Reason    string
	Kind      string
	Direction market.Direction
	Handle    float64
	At        int64
}

// Analysis is the complete outcome of one evaluation.
type Analysis struct {
	EvaluatedAt int64
	Handles     int
	Setups      []Setup
	Rejections  []Rejection
}

// Rejection reasons beyond the shared level-episode vocabulary (strategyutil.LevelReason*).
const (
	ReasonInvalidCandles          = "invalid_candles"
	ReasonInsufficientM5History   = "insufficient_m5_history"
	ReasonHandleLocked            = "handle_locked"
	ReasonInvalidatedAfterConfirm = "invalidated_after_confirmation"
	ReasonPriceThroughEntry       = "price_through_entry"
	ReasonInvalidStopGeometry     = "invalid_stop_geometry"
	ReasonNoTargetRoom            = "no_target_room"
	ReasonTargetAlreadyReached    = "target_already_reached"
	ReasonRiskExceedsEnvelope     = "risk_exceeds_execution_envelope"
	ReasonConfluenceBelowFloor    = "confluence_below_floor"
	ReasonSupersededBySibling     = "superseded_by_sibling"
)
