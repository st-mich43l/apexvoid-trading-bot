package breakretest

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// State is a position in the break-and-retest lifecycle. An episode is one
// accepted-or-attempted break of one reference structure; it moves forward
// only and ends in exactly one of the terminal states.
type State string

const (
	StateReferenceValid     State = "REFERENCE_VALID"
	StateBreakPending       State = "BREAK_PENDING"
	StateBreakAccepted      State = "BREAK_ACCEPTED"
	StateRetestWaiting      State = "RETEST_WAITING"
	StateRetestTouched      State = "RETEST_TOUCHED"
	StateRetestConfirmed    State = "RETEST_CONFIRMED"
	StateCandidate          State = "CANDIDATE"
	StateFalseBreakout      State = "FALSE_BREAKOUT"
	StateRetestFailed       State = "RETEST_FAILED"
	StateExpired            State = "EXPIRED"
	StateStructureInvalided State = "STRUCTURE_INVALIDATED"
)

// Terminal reports whether an episode in this state can no longer progress.
func (s State) Terminal() bool {
	switch s {
	case StateFalseBreakout, StateRetestFailed, StateExpired, StateStructureInvalided:
		return true
	}
	return false
}

// Reasons an episode ends, or a confirmed retest is not published.
const (
	ReasonInvalidCandles       = "invalid_candles"
	ReasonInsufficientHistory  = "insufficient_history"
	ReasonATRUnavailable       = "m5_atr_unavailable"
	ReasonBreakNotAccepted     = "break_not_accepted"
	ReasonBreakQuality         = "break_quality_insufficient"
	ReasonReclaimedBeforeRetst = "break_reclaimed_before_retest"
	ReasonRetestWindowExpired  = "retest_window_expired"
	ReasonConfirmWindowExpired = "confirmation_window_expired"
	ReasonRetestClosedThrough  = "retest_closed_through"
	ReasonProtectedLost        = "protected_structure_lost"
	ReasonNoProtected          = "no_protected_structure"
	ReasonInvalidatedAfter     = "invalidated_after_confirmation"
	ReasonConfirmationStale    = "confirmation_stale"
	ReasonPriceThroughEntry    = "price_through_entry"
	ReasonEntryTooFar          = "entry_too_far"
	ReasonNoOpposingStructure  = "no_opposing_structure"
	ReasonInsufficientRoom     = "insufficient_target_room"
	ReasonTargetReached        = "target_already_reached"
	ReasonDegenerateStop       = "degenerate_stop_geometry"
	ReasonRewardRisk           = "reward_risk_below_minimum"
	ReasonRiskEnvelope         = "risk_exceeds_execution_envelope"
	ReasonConfluenceFloor      = "confluence_below_floor"
	ReasonSuperseded           = "superseded_by_stronger_reference"
)

// Reference kinds.
const (
	KindKeyLevel  = "key_level"
	KindTrendline = "trendline"
)

// Reference is the structure that was broken: a horizontal key level built
// from same-side pivot touches, or a straight line through two same-side
// pivots. Every timestamp is a candle OPEN time (a bar closes 300 s later).
type Reference struct {
	Kind string
	ID   string
	// AnchorTime is the open time of the earliest pivot bar.
	AnchorTime int64
	// FormedAt is the open time of the bar whose close confirmed the pivot that
	// completed the reference (second touch for a level, second anchor for a
	// line): the first moment the reference was knowable.
	FormedAt int64
	// Touches is the number of pivot touches before the break (anchors included).
	Touches int
	// SideBefore is where price was before the break: "below" a broken
	// resistance (a BUY) or "above" a broken support (a SELL).
	SideBefore string
	// PriceAtBreak is the reference value at the first beyond-bar. Slope is the
	// line's price change per candle (0 for a level).
	PriceAtBreak float64
	Slope        float64
}

// Break is the accepted breakout: k consecutive closes beyond the reference by
// a buffer, with the first beyond-bar's measured force.
type Break struct {
	StartTime       int64 // first beyond-bar (open time)
	AcceptedAt      int64 // bar of the k-th accepted close (open time)
	AcceptCloses    int
	DistanceATR     float64 // acceptance close beyond the reference, in ATR
	BodyRatio       float64
	CloseStrength   float64
	DisplacementATR float64 // (close of the accepting bar - open of the first beyond-bar) / ATR
}

// Retest is the pullback to the broken reference and its rejection.
type Retest struct {
	TouchTime           int64
	ConfirmedAt         int64   // bar of the rejection candle (open time)
	Low                 float64 // extreme of the retest (oriented: the lowest low touch..confirm)
	DepthATR            float64 // how far through the reference the retest pierced, in ATR
	WickRatio           float64 // rejection candle's reaction wick / range
	CloseStrength       float64
	BarsAfterAcceptance int
}

// Setup is a confirmed, geometry-valid break-and-retest.
type Setup struct {
	Direction market.Direction
	Reference Reference
	Break     Break
	Retest    Retest

	EntryLow, EntryHigh float64
	Stop                float64
	Target              float64
	ProtectedStructure  float64
	ATR                 float64 // ATR at the confirmation bar
	RiskPips            float64
	RewardRisk          float64
	TargetRoomATR       float64
	WickRejection       bool
	ConfirmedAt         int64 // = Retest.ConfirmedAt
}

// Episode is the audit record of one break attempt and where it ended as of
// the evaluated bar.
type Episode struct {
	Direction  market.Direction
	Reference  Reference
	BreakStart int64
	AcceptedAt int64 // 0 until accepted
	State      State
	Reason     string // set for terminal states and for confirmed-but-unpublished retests
	EndedAt    int64  // open time of the bar that moved the episode to its current state
	Setup      *Setup // only for CANDIDATE
}

// Key identifies the episode independently of where it is evaluated.
func (e Episode) Key() string {
	return fmt.Sprintf("%s:%s:%d", e.Direction, e.Reference.ID, e.BreakStart)
}

// Analysis is everything one evaluation decided.
type Analysis struct {
	Setups   []Setup
	Episodes []Episode
}
