package strategyutil

import (
	"math"
	"sort"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Sweep kinds, from the most to the least authoritative liquidity. When several
// describe one event, the highest ranked one is the event's identity.
const (
	SweepSession = "session" // a finalized session, previous-day or previous-week extreme
	SweepEqual   = "equal"   // equal highs / equal lows
	SweepSwing   = "swing"   // a lone swing extreme
)

// LiquiditySweep is the observation that finalized liquidity was traded through and
// the market closed back: price went past the level (the stops resting there were
// taken) and a close returned to the origin side. It is a fact about what price did,
// not a signal: a sell-side sweep is evidence for a BUY thesis only if the strategy's
// own thesis needs it, and it never opens an opportunity by itself.
type LiquiditySweep struct {
	Kind  string
	Name  string  // ASIA_L, PDH, ... for a session level; the pool kind otherwise
	Level float64 // the price that was swept
	// SellSide is true when the liquidity rested below price (a low): its sweep is a
	// downward spike, evidence for a BUY. A high is buy-side liquidity.
	SellSide    bool
	AvailableAt int64 // when the liquidity became knowable (the level's period closed)
	SweepTime   int64 // open time of the candle that traded through the level
	ReclaimTime int64 // open time of the candle that closed back
	// PenetrationATR is how far the sweep went beyond the level, in detector ATR.
	PenetrationATR float64
}

// Supports reports whether the sweep is evidence for a trade in the direction: taking
// the stops below a low supports a BUY, taking the stops above a high a SELL.
func (s LiquiditySweep) Supports(direction market.Direction) bool {
	return s.SellSide && direction == market.Buy || !s.SellSide && direction == market.Sell
}

func sweepRank(kind string) int {
	switch kind {
	case SweepSession:
		return 0
	case SweepEqual:
		return 1
	}
	return 2
}

// SweepsSupporting are the confirmed sweeps of finalized liquidity that support the
// direction, whose sweep candle opened at or after from and whose reclaiming close
// completed at or before to (open times). Only information that existed at to is used:
// a level counts only from the close of its defining period (or the confirmation of its
// swing), the sweep must come after it, a later candle is never read, and a level whose
// first breach was NOT reclaimed within the reaction window is consumed, not swept.
//
// Several observations of one event - a session low that is also an equal-low pool, swept
// by the same candle - are one sweep: the most authoritative kind is kept and the rest are
// dropped, so the same price movement can never be counted as two independent facts.
func (d *LegacyDetector) SweepsSupporting(direction market.Direction, from, to int64) []LiquiditySweep {
	var found []LiquiditySweep
	for _, sweep := range append(d.sessionSweeps(to), d.poolSweeps(to)...) {
		if sweep.Supports(direction) && sweep.SweepTime >= from && sweep.ReclaimTime <= to {
			found = append(found, sweep)
		}
	}
	return dedupeSweeps(found, d.ATR*d.Settings.ProximalBandATR)
}

// dedupeSweeps merges sweeps of the same side whose levels lie within tolerance of one
// another and whose sweep candles are within one reaction of each other: one price event.
func dedupeSweeps(sweeps []LiquiditySweep, tolerance float64) []LiquiditySweep {
	sort.SliceStable(sweeps, func(i, j int) bool {
		if ri, rj := sweepRank(sweeps[i].Kind), sweepRank(sweeps[j].Kind); ri != rj {
			return ri < rj
		}
		if sweeps[i].SweepTime != sweeps[j].SweepTime {
			return sweeps[i].SweepTime < sweeps[j].SweepTime
		}
		return sweeps[i].Level < sweeps[j].Level
	})
	var out []LiquiditySweep
	for _, s := range sweeps {
		same := false
		for _, kept := range out {
			if kept.SellSide == s.SellSide && math.Abs(kept.Level-s.Level) <= tolerance && math.Abs(float64(kept.SweepTime-s.SweepTime)) <= float64(2*m5SecondsEvidence) {
				same = true
				break
			}
		}
		if !same {
			out = append(out, s)
		}
	}
	return out
}

const m5SecondsEvidence = 300

// sessionSweeps are the sweeps of finalized session-structure levels in the frame
// as of the open time `to`.
func (d *LegacyDetector) sessionSweeps(to int64) []LiquiditySweep {
	bars := d.Frame.Bars
	window := maxInt(1, d.Settings.ReactionLookbackBars)
	pip := d.Settings.PipSize
	var out []LiquiditySweep
	for _, level := range d.Frame.Sessions {
		if level.AvailableAt <= 0 || level.AvailableAt > to {
			continue // developing or not yet knowable: not liquidity to be swept
		}
		low := strings.HasSuffix(level.Name, "_L") || level.Name == "PDL" || level.Name == "PWL"
		if !low && !(strings.HasSuffix(level.Name, "_H") || level.Name == "PDH" || level.Name == "PWH") {
			continue
		}
		start := sort.Search(len(bars), func(i int) bool { return bars[i].Time >= level.AvailableAt })
		for k := start; k < len(bars) && bars[k].Time <= to; k++ {
			depth := level.Price - bars[k].Low
			if !low {
				depth = bars[k].High - level.Price
			}
			if depth <= pip {
				continue // a touch, not a sweep
			}
			// The first breach decides: reclaimed within the window it is a sweep, otherwise the
			// market accepted beyond the level and the liquidity is consumed.
			for r := k; r <= k+window && r < len(bars) && bars[r].Time <= to; r++ {
				if low && bars[r].Close >= level.Price || !low && bars[r].Close <= level.Price {
					atr := math.Max(d.ATR, 1e-12)
					out = append(out, LiquiditySweep{
						Kind: SweepSession, Name: level.Name, Level: level.Price, SellSide: low, AvailableAt: level.AvailableAt,
						SweepTime: bars[k].Time, ReclaimTime: bars[r].Time, PenetrationATR: depth / atr,
					})
					break
				}
			}
			break
		}
	}
	return out
}

// poolSweeps are the graded (A or B) grabs of the frame's equal-level and swing pools
// that completed by `to`, excluding inducement. A grade A/B grab is a wick through the
// pool that closes back on the same candle.
func (d *LegacyDetector) poolSweeps(to int64) []LiquiditySweep {
	bars := d.Frame.Bars
	var out []LiquiditySweep
	for _, grab := range d.Frame.Grabs {
		if grab.Inducement || grab.Grade != "A" && grab.Grade != "B" || grab.Index < 0 || grab.Index >= len(bars) || bars[grab.Index].Time > to {
			continue
		}
		pool := grab.Pool
		available := int64(0)
		if pool.LastIndex > 0 && pool.LastIndex < len(bars) {
			available = bars[pool.LastIndex].Time
		}
		if pool.LastIndex >= grab.Index {
			continue // the pool's own newest swing is the sweep candle or later: not yet liquidity
		}
		kind := SweepSwing
		if pool.Touches >= 2 {
			kind = SweepEqual
		}
		bar := bars[grab.Index]
		depth := pool.Level - bar.Low
		if pool.Side == "buy" {
			depth = bar.High - pool.Level
		}
		out = append(out, LiquiditySweep{
			Kind: kind, Name: kind + "_pool", Level: pool.Level, SellSide: pool.Side == "sell", AvailableAt: available,
			SweepTime: bar.Time, ReclaimTime: bar.Time, PenetrationATR: math.Max(0, depth) / math.Max(d.ATR, 1e-12),
		})
	}
	return out
}

// SessionSweepContext is the session-context confluence factor, measured, for a
// confirmed reaction: a finalized session, previous-day or previous-week level that
// supports the direction was swept and reclaimed within the reaction's own window (the
// reaction lookback before its touch, up to its confirmation).
func (d *LegacyDetector) SessionSweepContext(conf *LegacyConfirmation) bool {
	if d.Settings.FrozenSessionContext {
		return true
	}
	if conf == nil || conf.TouchIndex < 0 || conf.ConfirmationIndex >= len(d.Frame.Bars) {
		return false
	}
	first := maxInt(0, conf.TouchIndex-maxInt(1, d.Settings.ReactionLookbackBars))
	return d.SessionSweepBetween(d.Frame.Bars[first].Time, conf.ConfirmationTime)
}

// SessionSweepBetween reports whether a finalized session, previous-day or previous-week
// level supporting the direction was swept and reclaimed with the sweep candle opening at
// or after from and the reclaim at or before to. The frozen detectors set the session
// context factor on every decision, whatever happened (FrozenSessionContext).
func (d *LegacyDetector) SessionSweepBetween(from, to int64) bool {
	if d.Settings.FrozenSessionContext {
		return true
	}
	for _, sweep := range d.SweepsSupporting(d.Direction, from, to) {
		if sweep.Kind == SweepSession {
			return true
		}
	}
	return false
}
