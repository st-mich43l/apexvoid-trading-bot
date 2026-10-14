package rangeedge

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Range Edge is a mean-reversion strategy: it fades a boundary that the market
// has shown it respects, and returns toward the range's equilibrium. It has no
// business fading a boundary whose technical basis has failed. Everything here
// decides, from closed candles only, whether the edge a fade would trade against
// still is a range edge.
//
// One routine serves both sides. "Adverse" is the direction a fade loses in: up
// against a resistance (a SELL), down against a support (a BUY). Every comparison
// below is written once in terms of it, so BUY and SELL are exact mirrors.

// EdgeState is the technical state of the edge a fade would trade against.
type EdgeState string

const (
	// StateValid: the edge is intact; price may test it and be rejected.
	StateValid EdgeState = "range_valid"
	// StateBreakPending: price has closed beyond the edge fewer times than the
	// builder needs to call it broken, and has not yet come back.
	StateBreakPending EdgeState = "break_pending"
	// StateBroken: price accepted beyond the edge and has not reclaimed it. The
	// edge, and any edge rebuilt above (below) it by the sliding lookback, is retired.
	StateBroken EdgeState = "range_broken"
)

// Rejection reasons.
const (
	ReasonEdgeAcceptedBreak   = "edge_accepted_break"
	ReasonRangeNotReestablish = "range_not_reestablished"
	ReasonBreakPending        = "break_pending_unreclaimed"
	ReasonPressureIntoEdge    = "pressure_into_edge"
	ReasonRejectionNotInside  = "rejection_not_back_inside"
	ReasonReactionFailed      = "reaction_failed_after_confirmation"
	ReasonDataGap             = "data_gap"
	ReasonATRUnavailable      = "atr_unavailable"
)

// Integrity holds the coefficients; every one is read from configuration.
type Integrity struct {
	// ReclaimCloses consecutive closes back on the range side of a broken edge
	// restore it (the mirror of the builder's break_closes).
	ReclaimCloses int
	// MinimumInsideCloses closes inside a range re-established after a break.
	MinimumInsideCloses int
	// PressureBars candles ending at the confirming one, each with a higher low and
	// a higher close (mirrored for a support), rising at least PressureMinimumRiseATR
	// in total, are pressure into the edge, not a rejection of it.
	PressureBars           int
	PressureMinimumRiseATR float64
	// RejectionCloseStrength is where a candle must close in its range (1 = at the
	// extreme away from the edge) to be a rejection whatever the approach: a candle that
	// closes that decisively away from the edge is not pressure into it.
	RejectionCloseStrength float64
	// MaximumWidthATR bounds how far beyond a broken edge another cluster can lie and
	// still be part of the same range (the builder's maximum range width).
	MaximumWidthATR float64
	// MinimumReclaimATR is how far back inside the edge the confirming candle must
	// close, in ATR, once price has closed beyond the edge.
	MinimumReclaimATR float64
	// PeriodSeconds is the candle period, for data-gap detection.
	PeriodSeconds int64
}

// EdgeInput is everything the assessment reads.
type EdgeInput struct {
	Bars []market.Candle // closed candles up to and including the evaluated one
	ATR  float64
	// Sell is true when the fade is a SELL of a resistance edge.
	Sell           bool
	Edge, Opposite techniquezone.ScalpBarrier
	// Barriers are the live barriers of the structure (both sides); Broken the
	// edges price accepted through.
	Barriers     []techniquezone.ScalpBarrier
	Broken       []techniquezone.BrokenBarrier
	ConfirmIndex int // index in Bars of the confirming candle
	// GapBars is how many of the latest candles must be contiguous: the reaction window
	// and the pressure window (an older gap is the builder's business, not the reaction's).
	GapBars int
}

// Assessment is the edge's measured state and, when it is not tradable, why.
type Assessment struct {
	State  EdgeState
	Reject string

	// BreakDistanceATR is how far the latest close is beyond the edge level, in ATR
	// (positive = beyond it, adverse to the fade).
	BreakDistanceATR float64
	AcceptedCloses   int
	// RejectionCloseLocation is where the confirming candle closed in its range, 1 at
	// the extreme away from the edge (a rejection), 0 at the extreme into it.
	RejectionCloseLocation float64
	BodyATR                float64
	// PressureRiseATR is the rise (against the fade) over the pressure window.
	PressureRiseATR float64
	// ReclaimDepthATR is how far inside the edge the confirming candle closed.
	ReclaimDepthATR float64
	// RetiredBreakIndex is the bar of the accepted break that retired the edge, -1 if none.
	RetiredBreakIndex int
	// EpochIndex anchors the thesis identity: the bar of the latest unreclaimed
	// accepted break the edge sits beyond, or -1 when there is none.
	EpochIndex int
}

// adverse is +1 when the fade loses upward (a SELL), -1 downward (a BUY).
func adverse(sell bool) float64 {
	if sell {
		return 1
	}
	return -1
}

// beyond measures how far price is past a level in the adverse direction.
func beyond(sell bool, price, level float64) float64 { return adverse(sell) * (price - level) }

// Assess decides whether the edge is tradable. It reads only Bars[:len(Bars)],
// so the answer at a candle never depends on a later one.
func (p Integrity) Assess(in EdgeInput) Assessment {
	out := Assessment{State: StateValid, RetiredBreakIndex: -1, EpochIndex: -1, AcceptedCloses: in.Edge.AcceptedCloses}
	bars := in.Bars
	last := len(bars) - 1
	if last < 1 || !(in.ATR > 0) || math.IsNaN(in.ATR) || math.IsInf(in.ATR, 0) {
		out.Reject = ReasonATRUnavailable
		return out
	}
	ci := in.ConfirmIndex
	if ci < 0 || ci > last {
		ci = last
	}
	// A missing candle inside the recent window means the series is not a clean
	// market history: no acceptance run or pressure sequence is trusted across it.
	if p.PeriodSeconds > 0 && in.GapBars > 0 {
		for i := maxInt(1, last-in.GapBars+1); i <= last; i++ {
			if bars[i].Time-bars[i-1].Time > p.PeriodSeconds {
				out.Reject = ReasonDataGap
				return out
			}
		}
	}
	edge := in.Edge
	out.BreakDistanceATR = beyond(in.Sell, bars[last].Close, edge.Level) / in.ATR

	// The confirming candle, measured.
	c := bars[ci]
	span := c.High - c.Low
	if span > 0 {
		location := (c.Close - c.Low) / span
		if in.Sell {
			location = 1 - location
		}
		out.RejectionCloseLocation = location
	}
	out.BodyATR = math.Abs(c.Close-c.Open) / in.ATR
	out.ReclaimDepthATR = -beyond(in.Sell, c.Close, edge.Level) / in.ATR

	// 1. A retired edge. An edge the market accepted through is not an edge, and an
	// edge the sliding lookback rebuilds beyond it belongs to the same advance.
	side := "support"
	if in.Sell {
		side = "resistance"
	}
	for _, first := range in.Broken {
		if first.Side != side || first.BreakIndex < 0 || first.BreakIndex > last {
			continue
		}
		// Only the outermost established edge on its side counts: a cluster inside the
		// range that price runs through on its way to the edge is not an edge breaking.
		if !p.outermost(in, first) {
			continue
		}
		// The rebuilt edge is at or beyond the broken one.
		if beyond(in.Sell, edge.Level, first.Level) < -first.Tolerance {
			continue
		}
		// What the market did to it since: restored by a reclaim, retired again by a
		// later accepted run. The latest event decides, and its bar is the break.
		reclaimed, latest := techniquezone.BreakStatus(bars[:last+1], first, p.ReclaimCloses)
		if reclaimed {
			continue
		}
		b := first
		b.BreakIndex = latest
		if p.reestablished(in, b) {
			// A genuinely new range, formed after the break: its identity is the break.
			if out.EpochIndex < b.BreakIndex {
				out.EpochIndex = b.BreakIndex
			}
			continue
		}
		out.State = StateBroken
		out.Reject = ReasonEdgeAcceptedBreak
		if b.BreakIndex > out.RetiredBreakIndex {
			out.RetiredBreakIndex = b.BreakIndex
		}
		if edge.FirstTouchIndex > b.BreakIndex {
			out.Reject = ReasonRangeNotReestablish
		}
	}
	if out.Reject != "" {
		return out
	}

	// 2. A break pending: price has closed beyond the edge and has not come back. A
	// sweep that reclaims is the strategy's own thesis; a close still beyond is not.
	if edge.AcceptedCloses >= 1 {
		out.State = StateBreakPending
		back := -beyond(in.Sell, bars[last].Close, edge.Level) / in.ATR
		if !(back > 0) || back < p.MinimumReclaimATR {
			out.Reject = ReasonBreakPending
			return out
		}
		out.State = StateValid
	}

	// 2b. A later close beyond the edge fails the reaction: the rejection did not hold.
	for i := ci + 1; i <= last; i++ {
		if beyond(in.Sell, bars[i].Close, edge.Level) > 0 {
			out.Reject = ReasonReactionFailed
			return out
		}
	}

	// 3. The rejection must close back inside the edge.
	if beyond(in.Sell, c.Close, edge.Level) > 0 {
		out.Reject = ReasonRejectionNotInside
		return out
	}

	// 4. Pressure into the edge: a run of higher lows and higher closes (lower highs
	// and lower closes against a support) ending at the confirming candle is price
	// pressing the edge, and a candle that merely wicks it is not a rejection.
	if p.PressureBars >= 2 && ci >= p.PressureBars-1 {
		start := ci - p.PressureBars + 1
		pressed := true
		for i := start + 1; i <= ci && pressed; i++ {
			rising := adverse(in.Sell)*(bars[i].Close-bars[i-1].Close) > 0
			if in.Sell {
				pressed = rising && bars[i].Low >= bars[i-1].Low
			} else {
				pressed = rising && bars[i].High <= bars[i-1].High
			}
		}
		out.PressureRiseATR = adverse(in.Sell) * (bars[ci].Close - bars[start].Close) / in.ATR
		if pressed && out.PressureRiseATR >= p.PressureMinimumRiseATR && out.RejectionCloseLocation < p.RejectionCloseStrength {
			out.Reject = ReasonPressureIntoEdge
			return out
		}
	}
	return out
}

// outermost reports whether no other cluster of the same side, live or broken, that was
// already forming when b broke (its first touch is before the break) lay beyond it, above
// a resistance or below a support, by more than the tolerance and no further than the
// builder's maximum range width (a far cluster belongs to another range).
func (p Integrity) outermost(in EdgeInput, b techniquezone.BrokenBarrier) bool {
	reach := p.MaximumWidthATR * in.ATR
	beyondB := func(level float64) bool {
		d := beyondLevel(b.Side, level, b.Level)
		return d > b.Tolerance && (reach <= 0 || d <= reach)
	}
	for _, o := range in.Broken {
		if o.Side == b.Side && o.FirstTouchIndex < b.BreakIndex && beyondB(o.Level) {
			return false
		}
	}
	for _, o := range in.Barriers {
		if o.Side == b.Side && o.FirstTouchIndex < b.BreakIndex && beyondB(o.Level) {
			return false
		}
	}
	return true
}

// beyondLevel is how far level a is beyond level b on a side's adverse direction:
// above for a resistance, below for a support.
func beyondLevel(side string, a, b float64) float64 {
	if side == "resistance" {
		return a - b
	}
	return b - a
}

// floor is the opposite edge of the range now: the live barrier on the other side
// nearest price (the builder's selected opposite edge when none is).
func (in EdgeInput) floor() techniquezone.ScalpBarrier {
	price := in.Bars[len(in.Bars)-1].Close
	want := "support"
	if !in.Sell {
		want = "resistance"
	}
	best, found := techniquezone.ScalpBarrier{}, false
	for _, b := range in.Barriers {
		if b.Side != want || (in.Sell && b.Level > price) || (!in.Sell && b.Level < price) {
			continue
		}
		if !found || math.Abs(b.Level-price) < math.Abs(best.Level-price) {
			best, found = b, true
		}
	}
	if !found {
		return in.Opposite
	}
	return best
}

// reestablished reports whether a new range has formed since the break: both edges
// are made of contacts after it, and price has closed inside them at least
// MinimumInsideCloses times since. A stale opposite edge or a price that never came
// back inside is the old advance continuing, not a new range.
func (p Integrity) reestablished(in EdgeInput, b techniquezone.BrokenBarrier) bool {
	opposite := in.floor()
	if in.Edge.FirstTouchIndex <= b.BreakIndex || opposite.FirstTouchIndex <= b.BreakIndex {
		return false
	}
	lo, hi := math.Min(in.Edge.Level, opposite.Level), math.Max(in.Edge.Level, opposite.Level)
	inside := 0
	for i := b.BreakIndex + 1; i < len(in.Bars); i++ {
		if in.Bars[i].Close >= lo && in.Bars[i].Close <= hi {
			inside++
		}
	}
	return inside >= p.MinimumInsideCloses
}
