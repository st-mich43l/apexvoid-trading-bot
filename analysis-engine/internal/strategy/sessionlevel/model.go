package sessionlevel

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// State is a position in the Session Level lifecycle. An episode is one approach
// to (or sweep of) one completed reference level; it moves forward only and ends
// in exactly one of the terminal states.
type State string

const (
	StateLevelAvailable   State = "LEVEL_AVAILABLE"
	StateTouched          State = "TOUCHED"
	StateSwept            State = "SWEPT"
	StateReclaimWaiting   State = "RECLAIM_WAITING"
	StateReactionConfirm  State = "REACTION_CONFIRMED"
	StateCandidate        State = "CANDIDATE"
	StateBreakoutAccepted State = "BREAKOUT_ACCEPTED"
	StateReactionFailed   State = "REACTION_FAILED"
	StateExpired          State = "EXPIRED"
	StateEscalatedToSweep State = "ESCALATED_TO_SWEEP"
)

// Terminal reports whether an episode in this state can no longer progress.
func (s State) Terminal() bool {
	switch s {
	case StateBreakoutAccepted, StateReactionFailed, StateExpired, StateEscalatedToSweep:
		return true
	}
	return false
}

// Models are the two reaction models of the one Session Level strategy.
const (
	ModelFirstTouch   = "first_touch_rejection"
	ModelSweepReclaim = "sweep_reclaim"
	KindSession       = "session"
	KindDay           = "day"
	KindWeek          = "week"
	SideSupport       = "support"
	SideResistance    = "resistance"
	reasonNone        = ""
)

// Reasons an episode ends, a level is not usable, or a confirmed reaction is not
// published.
const (
	ReasonInvalidCandles       = "invalid_candles"
	ReasonATRUnavailable       = "m5_atr_unavailable"
	ReasonLevelSwept           = "level_already_swept"
	ReasonPriorTouches         = "level_touched_before"
	ReasonHistoryIndeterminate = "level_history_incomplete"
	ReasonNoApproach           = "no_fresh_approach"
	ReasonConfirmExpired       = "confirmation_window_expired"
	ReasonClosedThrough        = "closed_through_the_level"
	ReasonBreakoutAccepted     = "breakout_accepted"
	ReasonReclaimExpired       = "reclaim_window_expired"
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
	ReasonSuperseded           = "superseded_by_stronger_reaction"
	ReasonEpisodeLimit         = "episode_limit_per_level"
)

// Reasons a reference level is not built.
const (
	ReasonNoCandles       = "no_candles_in_period"
	ReasonStartIncomplete = "period_start_not_covered"
	ReasonEndIncomplete   = "period_end_not_covered"
	ReasonFeedGap         = "feed_gap_inside_period"
	ReasonLowCoverage     = "period_coverage_too_low"
	ReasonTooOld          = "level_too_old"
	ReasonNoHigherTF      = "higher_timeframe_unavailable"
)

// Level is one completed reference level, in real prices. Every timestamp is a
// candle OPEN time unless named otherwise.
type Level struct {
	ID   string // level:<NAME>:<periodStart>
	Name string // ASIA_H ASIA_L LONDON_H LONDON_L NY_H NY_L PDH PDL PWH PWL
	Kind string // session | day | week
	// Side is the role of the level for price below/above it: a high is resistance
	// (a SELL reaction), a low is support (a BUY reaction).
	Side  string
	Price float64
	// PeriodStart/PeriodEnd bound the period that defined the level, as UTC times.
	PeriodStart, PeriodEnd int64
	// SourceTF is the timeframe the extreme was read from.
	SourceTF market.Timeframe
	// FormedAt is the open time of the candle that made the extreme.
	FormedAt int64
	// AvailableAt is when the defining period completed: the first moment the level
	// is actionable. A forming session's provisional extreme is never a level.
	AvailableAt int64
	Candles     int
	Coverage    float64
	// ReachedAt is the open time of the first candle after availability that traded
	// to the level's price (0 when none did): an opposing level price has already
	// reached is no longer liquidity to aim at.
	ReachedAt int64
}

// Rejected is a reference period that could not become a level, and why.
type Rejected struct {
	Name   string
	Kind   string
	Start  int64
	End    int64
	Reason string
}

// Reaction is the confirmed reaction of one episode.
type Reaction struct {
	Model string
	// EpisodeAt is the touch candle (first-touch) or the sweep candle (sweep/reclaim).
	EpisodeAt   int64
	ConfirmedAt int64 // the rejection / reclaim candle
	// Extreme is the adverse extreme of the episode, oriented to the trade: the
	// lowest low from the touch/sweep to the confirmation for a BUY, the highest
	// high for a SELL. For a sweep it includes the sweep wick.
	Extreme float64
	// PenetrationATR is how far the touch pierced the level (first touch); 0 for none.
	PenetrationATR    float64
	SweepDepthATR     float64
	ReclaimDepthATR   float64
	RejectionStrength float64 // close location of the confirming candle (1 = at the extreme)
	WickRatio         float64
	BodyRatio         float64
	BarsToConfirm     int // from the touch/sweep to the confirming candle
}

// Setup is a confirmed, geometry-valid Session Level reaction.
type Setup struct {
	Direction market.Direction
	Level     Level
	Reaction  Reaction

	EntryLow, EntryHigh float64
	Stop                float64
	Target              float64
	TargetKind          string // swing | level
	ATR                 float64
	RiskPips            float64
	RewardRisk          float64
	TargetRoomATR       float64
	// PriorTouches is how many distinct earlier touches the level had since it
	// became available; TouchesKnown is false when the M5 history does not reach
	// back to the level's availability, in which case no count is claimed.
	PriorTouches int
	TouchesKnown bool
	LevelAgeBars int // M5 periods from the level's availability to the confirmation
	// LevelIntegrity is the share of M5 closes since availability that held the right
	// side of the level.
	LevelIntegrity  float64
	WickRejection   bool
	DisplacementATR float64
	ConfirmedAt     int64
}

// Episode is the audit record of one approach to a level and where it ended as of
// the evaluated bar.
type Episode struct {
	Direction market.Direction
	Level     Level
	Model     string
	EpisodeAt int64 // 0 until the touch / sweep
	State     State
	Reason    string
	EndedAt   int64
	Setup     *Setup
}

// Key identifies the episode independently of where it is evaluated.
func (e Episode) Key() string {
	return fmt.Sprintf("%s:%s:%s:%d", e.Direction, e.Level.ID, e.Model, e.EpisodeAt)
}

// Analysis is everything one evaluation decided.
type Analysis struct {
	Levels   []Level
	Rejected []Rejected
	Setups   []Setup
	Episodes []Episode
}
