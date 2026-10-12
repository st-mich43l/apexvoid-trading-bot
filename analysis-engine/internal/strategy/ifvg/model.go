package ifvg

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// State is a position in the iFVG lifecycle. An episode is one fair-value gap and
// the closing-price inversion that flipped its role; it moves forward only and
// ends in exactly one of the terminal states.
type State string

const (
	StateFVGCreated         State = "FVG_CREATED"
	StateInversionConfirmed State = "INVERSION_CONFIRMED"
	StateRetestWaiting      State = "RETEST_WAITING"
	StateRetestTouched      State = "RETEST_TOUCHED"
	StateRejectionConfirmed State = "REJECTION_CONFIRMED"
	StateCandidate          State = "CANDIDATE"
	StateGapInvalidated     State = "GAP_INVALIDATED"
	StateInversionFailed    State = "INVERSION_FAILED"
	StateRetestFailed       State = "RETEST_FAILED"
	StateExpired            State = "EXPIRED"
	StateStructureInvalided State = "STRUCTURE_INVALIDATED"
)

// Terminal reports whether an episode in this state can no longer progress.
func (s State) Terminal() bool {
	switch s {
	case StateGapInvalidated, StateInversionFailed, StateRetestFailed, StateExpired, StateStructureInvalided:
		return true
	}
	return false
}

// Reasons an episode ends, or a confirmed rejection is not published.
const (
	ReasonInvalidCandles      = "invalid_candles"
	ReasonATRUnavailable      = "m5_atr_unavailable"
	ReasonInversionBuffer     = "inversion_close_inside_buffer"
	ReasonInversionQuality    = "inversion_quality_insufficient"
	ReasonRoleFlipFailed      = "role_flip_failed"
	ReasonRetestWindowExpired = "retest_window_expired"
	ReasonConfirmExpired      = "confirmation_window_expired"
	ReasonRetestClosedThrough = "retest_closed_through"
	ReasonInvalidatedAfter    = "invalidated_after_confirmation"
	ReasonConfirmationStale   = "confirmation_stale"
	ReasonPriceThroughEntry   = "price_through_entry"
	ReasonEntryTooFar         = "entry_too_far"
	ReasonNoOpposingStructure = "no_opposing_structure"
	ReasonInsufficientRoom    = "insufficient_target_room"
	ReasonTargetReached       = "target_already_reached"
	ReasonDegenerateStop      = "degenerate_stop_geometry"
	ReasonRewardRisk          = "reward_risk_below_minimum"
	ReasonRiskEnvelope        = "risk_exceeds_execution_envelope"
	ReasonConfluenceFloor     = "confluence_below_floor"
	ReasonSuperseded          = "superseded_by_fresher_inversion"
	ReasonRetestLimit         = "retest_limit_per_inversion"
)

// Gap is the original fair-value gap, in real prices. Low/High bound the gap
// itself. Every timestamp is a candle OPEN time (a bar closes 300 s later).
type Gap struct {
	ID string
	// FormedAt is the open time of the third candle (C): the first moment the
	// gap was knowable.
	FormedAt int64
	// Role is what the gap was before it inverted: "bullish" supports price
	// (the inversion is bearish), "bearish" resists price (the inversion is
	// bullish).
	Role           string
	Low, High      float64
	WidthATR       float64 // gap width / ATR at its formation candle
	OriginBodyATR  float64 // middle candle body / ATR
	OriginBodyBias float64 // middle candle body / range
}

// Inversion is the qualified closing-price inversion of the gap.
type Inversion struct {
	Time            int64   // open time of the inverting candle
	BarsFromGap     int     // M5 periods from the gap's formation to the inversion
	CloseBeyondATR  float64 // how far the close finished beyond the gap's far bound, in ATR
	BodyRatio       float64
	CloseStrength   float64 // close location in the inversion direction (1 = at the extreme)
	DisplacementATR float64 // body / ATR
}

// Retest is the fresh return to the inverted gap and its rejection.
type Retest struct {
	TouchTime          int64
	ConfirmedAt        int64   // open time of the rejection candle
	Extreme            float64 // the retest's adverse extreme, oriented to the trade (lowest low for a buy)
	PenetrationATR     float64 // how far the retest pierced into the gap from its near bound, in ATR
	WickRatio          float64
	CloseStrength      float64
	BarsAfterInversion int
	Touches            int // distinct touches of the inverted gap since the inversion, this one included
}

// Setup is a confirmed, geometry-valid inverse fair-value gap.
type Setup struct {
	Direction market.Direction
	Gap       Gap
	Inversion Inversion
	Retest    Retest

	EntryLow, EntryHigh float64
	Stop                float64
	Target              float64
	ATR                 float64 // ATR at the confirmation bar
	RiskPips            float64
	RewardRisk          float64
	TargetRoomATR       float64
	ZoneIntegrity       float64 // 1 minus the deepest close inside the gap since the inversion, as a share of its width
	WickRejection       bool
	ConfirmedAt         int64 // = Retest.ConfirmedAt
}

// Episode is the audit record of one inversion and where it ended as of the
// evaluated bar.
type Episode struct {
	Direction  market.Direction
	Gap        Gap
	InvertedAt int64 // 0 until inverted
	TouchTime  int64 // 0 until the retest touches
	State      State
	Reason     string // set for terminal states and for confirmed-but-unpublished rejections
	EndedAt    int64  // open time of the bar that moved the episode to its current state
	Setup      *Setup // only for CANDIDATE
}

// Key identifies the episode independently of where it is evaluated: the gap, its
// inversion and the retest touch (zero while there is none).
func (e Episode) Key() string {
	return fmt.Sprintf("%s:%s:%d:%d", e.Direction, e.Gap.ID, e.InvertedAt, e.TouchTime)
}

// Thesis is the persistent identity of the structure: the gap and its inversion.
// A later confirmation of the same retest is the same thesis; a separate retest
// after the price has left again is a separate Episode (Key) of the same thesis.
func (e Episode) Thesis() string {
	return fmt.Sprintf("technique:ifvg:%s:%s:%d", e.Direction, e.Gap.ID, e.InvertedAt)
}

// Analysis is everything one evaluation decided.
type Analysis struct {
	Setups   []Setup
	Episodes []Episode
}

// ConfirmedAtOf is the rejection candle's open time for a published episode, 0 otherwise.
func (e Episode) ConfirmedAtOf() int64 {
	if e.Setup == nil {
		return 0
	}
	return e.Setup.ConfirmedAt
}
