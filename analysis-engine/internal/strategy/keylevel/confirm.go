package keylevel

import (
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Key Level owns its reaction confirmation (v10). It is the five-pattern evaluation every legacy-contract strategy shared (reaction.Evaluate), with the same
// pattern priority, windows and thresholds, plus three requirements that make the confirmation belong to THIS level and THIS reaction:
//
//  1. The level exists before it can be reacted to. The touch and the confirmation are bars at or after the bar on which the level became known (the confirmation
//     bar of the swing that completed its minimum count): a bar that is itself what completes the level cannot also be the reaction off it.
//  2. rejection_choch needs a change of character that belongs to the reaction: a CHoCH in the reaction direction on a bar from the touch bar to the confirmation bar.
//     One that broke before the level was touched is the trend the price arrived in, not part of the reaction, and the candidate is then the pattern the bar
//     actually shows (strong_reclaim or wick_rejection).
//  3. A rejection happens AT the level. A wick rejection needs the confirmation bar to reach the band (its rejecting wick is the band's); an engulfing needs the
//     engulfing bar or the bar it engulfs to reach it. A hammer or an engulfing candle formed away from the band after an earlier touch is not a reaction of the level.
//
// sweep_reclaim (a grab of this level's own pool, on the confirmation bar) and strong_reclaim (the bar pierces the band's far edge, so it touches by construction)
// are unchanged. The shared evaluation, which the other strategies still use, is untouched.

const (
	patternWickRejection  = reaction.TypeWickRejection
	patternSweepReclaim   = reaction.TypeSweepReclaim
	patternRejectionCHoCH = reaction.TypeRejectionCHoCH
	patternStrongReclaim  = reaction.TypeStrongReclaim
	patternEngulfing      = reaction.TypeEngulfing

	epsilonPrice = 1e-12
)

// confirmParams are one evaluation's inputs beyond the candle window.
type confirmParams struct {
	Direction                string // "BUY" | "SELL"
	Low, High                float64
	TouchLookback            int
	ConfirmLookback          int
	GrabIndex                int // the bar of this level's grab, or -1
	GrabGrade                string
	CHoCHIndexes             []int // bars of the CHoCH breaks in the reaction direction
	ATR                      float64
	EngulfingMinimumRangeATR float64
	MinIndex                 int // the bar on which the level became known
	// AllowUnconfirmed returns the latest touch as an unconfirmed reaction (Type "") when no pattern confirms it (analysis.require_candle_confirmation: false).
	AllowUnconfirmed bool
}

// evaluateConfirmation finds the latest valid confirmation within the lookback windows, or nil. Touch and confirmation may be on different bars; the
// confirmation is on or after its touch, and both are at or after MinIndex.
func evaluateConfirmation(candles []market.Candle, p confirmParams) *strategyutil.LegacyConfirmation {
	if len(candles) == 0 {
		return nil
	}
	touchLB := p.TouchLookback
	if touchLB < 1 {
		touchLB = 1
	}
	confirmLB := touchLB
	if p.ConfirmLookback > 0 {
		confirmLB = p.ConfirmLookback
	}
	last := len(candles) - 1
	touchEarliest := maxInt(maxInt(0, p.MinIndex), last-touchLB+1)
	confirmEarliest := maxInt(maxInt(0, p.MinIndex), last-confirmLB+1)
	touches := func(i int) bool {
		return i >= 0 && i <= last && candles[i].Low <= p.High+epsilonPrice && candles[i].High >= p.Low-epsilonPrice
	}
	var touchIndexes []int
	for i := touchEarliest; i <= last; i++ {
		if touches(i) {
			touchIndexes = append(touchIndexes, i)
		}
	}
	if len(touchIndexes) == 0 {
		return nil
	}
	// The latest valid confirmation, so a stale touch without a fresh confirmation never executes.
	for ci := last; ci >= confirmEarliest; ci-- {
		touchIndex := -1
		for _, ti := range touchIndexes {
			if ti <= ci {
				touchIndex = ti
			}
		}
		if touchIndex < 0 {
			continue
		}
		bar := candles[ci]
		wick := wickRejection(bar, p.Direction) && touches(ci)
		var kind string
		switch {
		case p.GrabIndex == ci && (p.GrabGrade == "A" || p.GrabGrade == "B"):
			kind = patternSweepReclaim
		case wick && chochBetween(p.CHoCHIndexes, touchIndex, ci):
			kind = patternRejectionCHoCH
		case strongReclaim(bar, p.Direction, p.Low, p.High):
			kind = patternStrongReclaim
		case wick:
			kind = patternWickRejection
		case ci > 0 && engulfing(bar, candles[ci-1], p.Direction, p.ATR, p.EngulfingMinimumRangeATR) && (touches(ci) || touches(ci-1)):
			kind = patternEngulfing
		}
		if kind == "" {
			continue
		}
		return &strategyutil.LegacyConfirmation{
			Type: kind, TouchIndex: touchIndex, ConfirmationIndex: ci, TouchTime: candles[touchIndex].Time, ConfirmationTime: candles[ci].Time,
		}
	}
	if p.AllowUnconfirmed {
		latest := touchIndexes[len(touchIndexes)-1]
		return &strategyutil.LegacyConfirmation{Type: "", TouchIndex: latest, ConfirmationIndex: latest, TouchTime: candles[latest].Time, ConfirmationTime: candles[latest].Time}
	}
	return nil
}

// chochBetween reports whether a CHoCH in the reaction direction broke on a bar from the touch bar to the confirmation bar.
func chochBetween(indexes []int, touchIndex, confirmationIndex int) bool {
	for _, i := range indexes {
		if i >= touchIndex && i <= confirmationIndex {
			return true
		}
	}
	return false
}

// reactionCHoCH reports whether the detector's frame broke character in the trade's direction on a bar of the reaction itself, from the touch bar to the confirmation
// bar: the same attribution that names a rejection_choch pattern. A break before the touch preceded the reaction and one after the confirmation followed it; neither is
// evidence this reaction reversed the market.
func reactionCHoCH(d *strategyutil.LegacyDetector, touchIndex, confirmationIndex int) bool {
	wanted := "down"
	if d.Direction == market.Buy {
		wanted = "up"
	}
	for _, brk := range d.Frame.Breaks {
		if brk.Kind == "CHoCH" && brk.Direction == wanted && brk.Index >= touchIndex && brk.Index <= confirmationIndex {
			return true
		}
	}
	return false
}

// confirmReaction is the Key Level reaction off the band [low, high] of a level that became known on bar knownFrom, in the detector's direction. grab is this
// level's own liquidity grab (strategyutil.LevelGrab: a pool within the level's tolerance), if any.
func (s *Strategy) confirmReaction(d *strategyutil.LegacyDetector, low, high float64, grab *techniquezone.Grab, knownFrom int) *strategyutil.LegacyConfirmation {
	lookback := maxInt(1, d.Settings.ReactionLookbackBars)
	wanted := "down"
	if d.Direction == market.Buy {
		wanted = "up"
	}
	var choch []int
	for _, brk := range d.Frame.Breaks {
		if brk.Kind == "CHoCH" && brk.Direction == wanted {
			choch = append(choch, brk.Index)
		}
	}
	p := confirmParams{
		Direction: string(d.Direction), Low: low, High: high, TouchLookback: lookback, ConfirmLookback: lookback, GrabIndex: -1,
		CHoCHIndexes: choch, ATR: d.ATR, EngulfingMinimumRangeATR: d.Settings.EngulfingMinimumRangeATR, MinIndex: knownFrom, AllowUnconfirmed: d.Settings.CandleConfirmationOptional,
	}
	if grab != nil {
		p.GrabIndex, p.GrabGrade = grab.Index, grab.Grade
	}
	return evaluateConfirmation(d.Frame.Bars, p)
}

// levelKnownFrom is the bar on which the level became known: the confirmation bar of the swing that brought the swings in its band to minimumTouches (a fractal swing is
// known only when its right-hand bars have closed). A level with no swing in its band (a measurement hook's level) has no formation to wait for: 0.
func levelKnownFrom(frame *context.LegacyFrame, price, band float64, minimumTouches int) int {
	var confirmed []int
	for _, sw := range frame.Swings {
		if sw.Price-price <= band && price-sw.Price <= band {
			confirmed = append(confirmed, sw.ConfirmedIndex)
		}
	}
	if len(confirmed) == 0 {
		return 0
	}
	sort.Ints(confirmed)
	k := minInt(maxInt(1, minimumTouches), len(confirmed))
	return confirmed[k-1]
}

// wickRejection: the rejecting wick is at least the body and the bar closes in its reaction-side third, in the reaction direction.
func wickRejection(c market.Candle, direction string) bool {
	return reaction.WickRejection(c, direction)
}

// strongReclaim: the wick pierces the band's far edge (the lower edge for a BUY, the upper for a SELL), then the bar closes back at or inside that edge in the reaction
// direction: a candle that swept the band and took it back. It is a reclaim of the far edge, not of the whole band: the close may lie inside the band or beyond its near edge.
func strongReclaim(c market.Candle, direction string, low, high float64) bool {
	if direction == "BUY" {
		return c.Low < low-epsilonPrice && c.Close >= low-epsilonPrice && c.Close > c.Open
	}
	return c.High > high+epsilonPrice && c.Close <= high+epsilonPrice && c.Close < c.Open
}

// engulfing: this bar's body fully engulfs the prior bar's body, the prior bar was a doji or opposed the reaction, and (with an ATR) the bar has real range.
func engulfing(c, prior market.Candle, direction string, atr, minimumRangeATR float64) bool {
	bodyLow, bodyHigh := minFloat(c.Open, c.Close), maxFloat(c.Open, c.Close)
	priorBodyLow, priorBodyHigh := minFloat(prior.Open, prior.Close), maxFloat(prior.Open, prior.Close)
	if bodyLow > priorBodyLow || bodyHigh < priorBodyHigh {
		return false
	}
	priorRange := maxFloat(prior.High-prior.Low, epsilonPrice)
	priorBodyFraction := abs(prior.Close-prior.Open) / priorRange
	priorBullish := prior.Close > prior.Open+epsilonPrice
	priorBearish := prior.Close < prior.Open-epsilonPrice
	priorOpposite := priorBodyFraction < 0.1 || (direction == "BUY" && priorBearish) || (direction == "SELL" && priorBullish)
	if !priorOpposite {
		return false
	}
	if atr > 0 && (c.High-c.Low) < maxFloat(0, minimumRangeATR)*atr {
		return false
	}
	if direction == "BUY" {
		return c.Close > c.Open
	}
	return c.Close < c.Open
}

func abs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func minFloat(a, b float64) float64 {
	if a < b {
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
