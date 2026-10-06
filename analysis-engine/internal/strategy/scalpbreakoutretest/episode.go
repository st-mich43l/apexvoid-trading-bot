package scalpbreakoutretest

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Breakout episode states and failure reasons. The names are the frozen
// Python engine's (app/scalping/models.py BR_*), kept so replay output and
// telemetry read the same.
const (
	stateWatchLevel    = "WATCH_LEVEL"
	stateBreakDetected = "BREAK_DETECTED"
	stateAccepted      = "ACCEPTED"
	stateWaitRetest    = "WAIT_RETEST"
	stateRetested      = "RETESTED"
	stateConfirmation  = "CONFIRMATION"
	stateArmed         = "ARMED"
	stateFailedBreak   = "FAILED_BREAK"
	stateExpired       = "EXPIRED"
	stateInvalidRetest = "INVALID_RETEST"
	stateNoContinue    = "NO_CONTINUATION"

	reasonNoCandidateLevel     = "no_candidate_level"
	reasonBreakNotAfterSource  = "break_not_after_source"
	reasonFailedAcceptance     = "failed_acceptance"
	reasonImmediateReclaim     = "immediate_reclaim"
	reasonOppositeStructure    = "opposite_structure_break"
	reasonRetestTooDeep        = "retest_too_deep"
	reasonRetestTooLate        = "retest_too_late"
	reasonNoRoleFlip           = "no_role_flip"
	reasonNoConfirmation       = "no_confirmation"
	confirmationRetestReclaim  = "retest_close_reclaim"
	confirmationRetestHighBrk  = "retest_high_break"
	confirmationRetestLowBrk   = "retest_low_break"
	confirmationModeHighBreak  = "retest_high_break"
	sourceCompressionBox       = "compression_box"
	sourceM5SwingHigh          = "m5_swing_high"
	sourceM5SwingLow           = "m5_swing_low"
	sourceM1SwingHigh          = "m1_swing_high"
	sourceM1SwingLow           = "m1_swing_low"
	sourceEQH                  = "eqh"
	sourceEQL                  = "eql"
	subtypeRangeBreak          = "range_break"
	subtypeStructureFlip       = "structure_flip"
	subtypeLiquidityLevelBreak = "liquidity_level_break"
)

// levelCandidate is one structural level a breakout can be measured against.
type levelCandidate struct {
	Level     float64
	Side      market.Direction
	Source    string
	Subtype   string
	Timeframe string
	// SourceIndex is the bar the level was formed on; nil for sources without
	// one (M5 key levels, equal highs/lows), which may be broken at any bar.
	SourceIndex *int
	// Invalidation is the far edge of a compression box (nil otherwise).
	Invalidation *float64
}

// episode is the telemetry of one candidate run through the state machine.
type episode struct {
	State         string
	FailureReason string

	BreakIndex      int
	BreakTime       int64
	BreakDistance   float64
	BreakDistanceAT *float64

	Accepted       bool
	AcceptanceBars int

	RetestIndex      int
	BarsUntilRetest  *int
	RetestPenetrate  *float64
	RetestPenATR     *float64
	RetestTime       int64
	ConfirmationType string

	DirectionallyValidClose bool
}

// episodeParams are the V2 knobs (strategies.scalping.breakout.*).
type episodeParams struct {
	ATR                  float64
	CrossTolerance       float64
	BreakoutMargin       float64
	MaxBreakDelayBars    *int
	AcceptanceBars       int
	AcceptanceRequired   int
	MinRetestDelayBars   int
	MaxRetestDelayBars   int
	RetestFrontRun       float64
	MaxRetestPenetration float64
	ConfirmationMode     string
}

func isTrueLevelCross(side market.Direction, prevClose, close, level, crossTolerance, margin float64) bool {
	tol := math.Max(0, crossTolerance)
	mar := math.Max(0, margin)
	if side == market.Buy {
		return prevClose <= level+tol && close > level+mar
	}
	return prevClose >= level-tol && close < level-mar
}

type acceptance struct {
	Accepted bool
	Pending  bool
	Elapsed  int
	Reason   string
}

func evaluateAcceptance(bars []market.Candle, side market.Direction, level float64, breakIndex, acceptanceBars, requiredCloses int, invalidation *float64) acceptance {
	n := len(bars)
	windowLen := acceptanceBars
	if windowLen < 1 {
		windowLen = 1
	}
	end := breakIndex + 1 + windowLen
	if end > n {
		end = n
	}
	closesBeyond := 0
	immediateReclaim := false
	elapsed := 0
	for offset, i := 0, breakIndex+1; i < end; offset, i = offset+1, i+1 {
		elapsed = offset + 1
		close := bars[i].Close
		if invalidation != nil {
			breached := side == market.Buy && close < *invalidation || side == market.Sell && close > *invalidation
			if breached {
				return acceptance{Elapsed: offset + 1, Reason: reasonOppositeStructure}
			}
		}
		beyond := side == market.Buy && close > level || side == market.Sell && close < level
		if beyond {
			closesBeyond++
		} else if offset == 0 {
			immediateReclaim = true
		}
	}
	required := requiredCloses
	if required < 1 {
		required = 1
	}
	if closesBeyond >= required {
		return acceptance{Accepted: true, Elapsed: elapsed}
	}
	if immediateReclaim {
		return acceptance{Elapsed: elapsed, Reason: reasonImmediateReclaim}
	}
	if elapsed < windowLen {
		return acceptance{Pending: true, Elapsed: elapsed}
	}
	return acceptance{Elapsed: elapsed, Reason: reasonFailedAcceptance}
}

type retest struct {
	Index       int // -1 when none
	BarsUntil   int
	Penetration float64
	HasPen      bool
	Reason      string
}

func evaluateRetest(bars []market.Candle, side market.Direction, level float64, breakIndex int, p episodeParams) retest {
	n := len(bars)
	tolerance := math.Max(0, p.RetestFrontRun)
	maxPen := math.Max(0, p.MaxRetestPenetration)
	earliest := breakIndex + 1 + maxInt(0, p.MinRetestDelayBars)
	latest := breakIndex + maxInt(1, p.MaxRetestDelayBars)
	start := maxInt(earliest, breakIndex+1)
	stop := minInt(n, latest+1)
	for i := start; i < stop; i++ {
		high, low := bars[i].High, bars[i].Low
		var touched bool
		var penetration float64
		if side == market.Buy {
			touched = low <= level+tolerance
			penetration = math.Max(0, level-low)
		} else {
			touched = high >= level-tolerance
			penetration = math.Max(0, high-level)
		}
		if !touched {
			continue
		}
		if penetration > maxPen {
			return retest{Index: -1, Penetration: penetration, HasPen: true, Reason: reasonRetestTooDeep}
		}
		return retest{Index: i, BarsUntil: i - breakIndex, Penetration: penetration, HasPen: true}
	}
	if n-1 < latest {
		return retest{Index: -1}
	}
	return retest{Index: -1, Reason: reasonRetestTooLate}
}

type retestConfirmation struct {
	Confirmed bool
	Pending   bool
	Type      string
	Reason    string
}

func evaluateRetestConfirmation(bars []market.Candle, side market.Direction, level float64, retestIndex int, mode string) retestConfirmation {
	n := len(bars)
	retestBar := bars[retestIndex]
	roleFlip := side == market.Buy && retestBar.Close > level || side == market.Sell && retestBar.Close < level
	if !roleFlip {
		return retestConfirmation{Reason: reasonNoRoleFlip}
	}
	if mode != confirmationModeHighBreak {
		return retestConfirmation{Confirmed: true, Type: confirmationRetestReclaim}
	}
	if retestIndex+1 >= n {
		return retestConfirmation{Pending: true}
	}
	confirmBar := bars[retestIndex+1]
	if side == market.Buy && confirmBar.High > retestBar.High {
		return retestConfirmation{Confirmed: true, Type: confirmationRetestHighBrk}
	}
	if side == market.Sell && confirmBar.Low < retestBar.Low {
		return retestConfirmation{Confirmed: true, Type: confirmationRetestLowBrk}
	}
	return retestConfirmation{Reason: reasonNoConfirmation}
}

// evaluateEpisode runs one level candidate through the full breakout state
// machine: break → acceptance → retest → role-flip confirmation → armed. It
// is pure and never reads past the last bar it is given.
func evaluateEpisode(bars []market.Candle, c levelCandidate, p episodeParams) episode {
	result := episode{State: stateWatchLevel, BreakIndex: -1, RetestIndex: -1}
	n := len(bars)
	if n < 2 {
		result.FailureReason = reasonNoCandidateLevel
		return result
	}
	level := c.Level
	side := c.Side
	earliest := 1
	if c.SourceIndex != nil {
		earliest = maxInt(1, *c.SourceIndex+1)
	}
	breakIndex := -1
	for i := earliest; i < n; i++ {
		if isTrueLevelCross(side, bars[i-1].Close, bars[i].Close, level, p.CrossTolerance, p.BreakoutMargin) {
			breakIndex = i
			break
		}
	}
	if breakIndex < 0 {
		return result
	}
	if p.MaxBreakDelayBars != nil && c.SourceIndex != nil && breakIndex > *c.SourceIndex+*p.MaxBreakDelayBars {
		result.State, result.FailureReason = stateExpired, reasonBreakNotAfterSource
		return result
	}
	breakBar := bars[breakIndex]
	distance := breakBar.Close - level
	if side == market.Sell {
		distance = level - breakBar.Close
	}
	result.State = stateBreakDetected
	result.BreakIndex, result.BreakTime, result.BreakDistance = breakIndex, breakBar.Time, distance
	if p.ATR > 0 {
		v := distance / p.ATR
		result.BreakDistanceAT = &v
	}

	acc := evaluateAcceptance(bars, side, level, breakIndex, p.AcceptanceBars, p.AcceptanceRequired, c.Invalidation)
	result.Accepted, result.AcceptanceBars = acc.Accepted, acc.Elapsed
	if !acc.Accepted {
		if acc.Pending {
			return result
		}
		result.State, result.FailureReason = stateFailedBreak, acc.Reason
		return result
	}
	result.State = stateAccepted

	rt := evaluateRetest(bars, side, level, breakIndex, p)
	if rt.HasPen {
		pen := rt.Penetration
		result.RetestPenetrate = &pen
		if p.ATR > 0 {
			v := pen / p.ATR
			result.RetestPenATR = &v
		}
	}
	if rt.Index < 0 {
		if rt.Reason == "" {
			result.State = stateWaitRetest
			return result
		}
		if rt.Reason == reasonRetestTooDeep {
			result.State = stateInvalidRetest
		} else {
			result.State = stateExpired
		}
		result.FailureReason = rt.Reason
		return result
	}
	until := rt.BarsUntil
	result.BarsUntilRetest = &until
	result.RetestIndex, result.RetestTime = rt.Index, bars[rt.Index].Time
	result.State = stateRetested

	confirm := evaluateRetestConfirmation(bars, side, level, rt.Index, p.ConfirmationMode)
	result.ConfirmationType = confirm.Type
	if !confirm.Confirmed {
		if confirm.Pending {
			return result
		}
		result.State, result.FailureReason = stateNoContinue, confirm.Reason
		return result
	}
	result.State = stateConfirmation
	last := bars[n-1].Close
	result.DirectionallyValidClose = side == market.Buy && last > level || side == market.Sell && last < level
	result.State = stateArmed
	return result
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
