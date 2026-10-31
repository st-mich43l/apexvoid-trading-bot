package orderblock

import (
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The sweep test (docs/strategies/liquidity-sweep-and-structure.md). Did the displacement that made a block follow a sweep of resting liquidity in its favour?
//
// For an impulse whose first candle is bar s of the block's own frame (the first candle of the displacement leg that broke structure), in favour of a side, within the sweep_lookback_bars before s (s included) look for a
// bar that traded through a level whose liquidity rests on the other side of the move: for a BUY, below a level (sell-side liquidity), for a SELL, above
// one. Every level is known before the bar that sweeps it:
//
//   - swing: a fractal pivot of this strategy's own (sweep_pivot_bars on each side) already confirmed at the bar before, formed within
//     sweep_level_max_age_bars of the sweeping bar and not traded through by any bar since its confirmation;
//   - pdl / pdh: the previous trading day's low (high), finalized and available at the open of the sweeping bar;
//   - session: a finalized Asia or London low (high) available at the open of the sweeping bar.
//
// The earliest sweeping bar decides; when one bar sweeps several kinds, pdl/pdh outranks session outranks swing, and within a kind the deepest sweep. The
// strategy owns this copy (order_block has its own); nothing after the bars of the frame is read.

const (
	sweepKindSwing   = "swing"
	sweepKindDay     = "day" // reported as "pdl" for a BUY and "pdh" for a SELL
	sweepKindSession = "session"
)

// sweep is the result of the sweep test.
type sweep struct {
	Found bool
	// Kind is swing | pdl | pdh | session.
	Kind string
	// Bar is the index, on the block's frame, of the sweeping bar; Level the price it traded through.
	Bar   int
	Level float64
	// Depth is how far beyond the level the bar went, in the frame's ATR at that bar; Reclaimed whether the bar closed back beyond the level.
	Depth     float64
	Reclaimed bool
}

// sweepLevel is one resting level and what kind it is.
type sweepLevel struct {
	kind  string
	price float64
	rank  int // 3 day, 2 session, 1 swing
}

// priorSweep runs the sweep test for the impulse starting at bar start of frame, in favour of a BUY (buy) or a SELL. fallbackATR is the frame's median
// ATR, used where the series has no value.
func (s *Strategy) priorSweep(frame *analysiscontext.LegacyFrame, start int, buy bool, fallbackATR float64) sweep {
	bars := frame.Bars
	if start < 0 || start >= len(bars) {
		return sweep{}
	}
	lookback, n, maxAge := s.cfg.SweepLookbackBars, s.cfg.SweepPivotBars, s.cfg.SweepLevelMaxAgeBars
	first := maxInt(0, start-lookback)
	pivots := fractalPivots(frame, maxInt(0, first-maxAge-n), start-1, n, buy)
	for k := first; k <= start; k++ {
		bar := bars[k]
		var best sweepLevel
		bestDepth := math.Inf(-1)
		consider := func(level sweepLevel) {
			through := bar.Low < level.price
			if !buy {
				through = bar.High > level.price
			}
			if !through {
				return
			}
			depth := level.price - bar.Low
			if !buy {
				depth = bar.High - level.price
			}
			if level.rank > best.rank || level.rank == best.rank && depth > bestDepth {
				best, bestDepth = level, depth
			}
		}
		for _, ref := range frame.Sessions {
			kind, ok := sessionLevelKind(ref.Name, buy)
			if !ok || ref.AvailableAt <= 0 || ref.AvailableAt > bar.Time {
				continue
			}
			rank := 2
			if kind == sweepKindDay {
				rank = 3
			}
			consider(sweepLevel{kind: kind, price: ref.Price, rank: rank})
		}
		for _, p := range pivots {
			if p+n > k-1 || k-p > maxAge || tradedThrough(bars, p+n, k-1, bars[p], buy) {
				continue
			}
			price := bars[p].Low
			if !buy {
				price = bars[p].High
			}
			consider(sweepLevel{kind: sweepKindSwing, price: price, rank: 1})
		}
		if best.rank == 0 {
			continue
		}
		kind := best.kind
		if kind == sweepKindDay {
			kind = "pdl"
			if !buy {
				kind = "pdh"
			}
		}
		atr := atrAt(frame.ATR, k, fallbackATR)
		depth := 0.0
		if atr > 0 {
			depth = bestDepth / atr
		}
		reclaimed := bar.Close > best.price
		if !buy {
			reclaimed = bar.Close < best.price
		}
		return sweep{Found: true, Kind: kind, Bar: k, Level: best.price, Depth: depth, Reclaimed: reclaimed}
	}
	return sweep{}
}

// sessionLevelKind maps a session reference to the sweep kind it can be for the side: the previous day's low / high, or an Asia / London low / high.
func sessionLevelKind(name string, buy bool) (string, bool) {
	switch {
	case buy && name == "PDL", !buy && name == "PDH":
		return sweepKindDay, true
	case buy && (name == "ASIA_L" || name == "LONDON_L"), !buy && (name == "ASIA_H" || name == "LONDON_H"):
		return sweepKindSession, true
	}
	return "", false
}

// fractalPivots are the indexes in [from, to] of the bars that are a fractal low (BUY) or high (SELL) of n bars on each side: strictly beyond the n bars
// before it and not beyond the n bars after it.
func fractalPivots(frame *analysiscontext.LegacyFrame, from, to, n int, buy bool) []int {
	bars := frame.Bars
	var out []int
	for p := maxInt(from, n); p <= to && p+n < len(bars); p++ {
		pivot := true
		for j := p - n; j <= p+n && pivot; j++ {
			if j == p {
				continue
			}
			switch {
			case buy && j < p:
				pivot = bars[j].Low > bars[p].Low
			case buy:
				pivot = bars[j].Low >= bars[p].Low
			case j < p:
				pivot = bars[j].High < bars[p].High
			default:
				pivot = bars[j].High <= bars[p].High
			}
		}
		if pivot {
			out = append(out, p)
		}
	}
	return out
}

// tradedThrough reports whether any bar in [from, to] went beyond the pivot bar's extreme.
func tradedThrough(bars []market.Candle, from, to int, pivot market.Candle, buy bool) bool {
	for j := from; j <= to; j++ {
		if buy && bars[j].Low < pivot.Low || !buy && bars[j].High > pivot.High {
			return true
		}
	}
	return false
}
