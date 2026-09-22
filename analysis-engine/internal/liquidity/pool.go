package liquidity

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// LiquiditySide is which side of price a pool's resting liquidity sits on
// — buy-side liquidity (stops/orders resting ABOVE price, at a high) or
// sell-side liquidity (resting BELOW price, at a low). Source task §30.
type LiquiditySide uint8

const (
	LiquidityBuySide LiquiditySide = iota
	LiquiditySellSide
)

func (s LiquiditySide) String() string {
	if s == LiquidityBuySide {
		return "buy_side"
	}
	return "sell_side"
}

// Pool is one liquidity concentration — source task §30, extended (per
// §31's own prose requirement to "track touch count, first touch, last
// touch, price band") with TouchCount/FirstTouchAt/LastTouchAt, which
// §30's illustrative snippet omitted but §31 explicitly asks for.
//
// Layer reuses structure.StructureLayer — a pool anchored at a Major swing
// carries different weight than one at a Micro swing (this is why
// liquidity depends on structure in docs/architecture/dependency-rules.md,
// an amendment made when this package was implemented, not part of the
// original architecture freeze). This is deliberately a DIFFERENT concept
// from the source task's own "Internal Liquidity"/"External Liquidity"
// pool categories (§29) — those describe a pool's position relative to
// the CURRENT dealing range (inside it vs. at its extremes), not its
// structural-hierarchy significance; see Source below for that
// distinction, and docs/analysis/market-structure-v2.md for the full
// writeup.
type Pool struct {
	ID string

	Side LiquiditySide

	Low  market.Price
	High market.Price

	Layer structure.StructureLayer

	// Source names what produced this pool: "swing_high"/"swing_low" (one
	// swing) or "equal_high"/"equal_low" (a cluster — see equal_high_low.go).
	// Session-high/session-low and internal/external dealing-range
	// classification (§29's remaining categories) are NOT produced by
	// this pass — recorded as not-yet-implemented in
	// docs/analysis-engine-v2-migration.md, not silently dropped.
	Source string

	CreatedAt int64

	TouchCount   int
	FirstTouchAt int64
	LastTouchAt  int64

	// SweptAt is set the first time price trades beyond this pool's outer
	// edge (High for buy-side, Low for sell-side) — sweeping IS the
	// completed event for a pool; there is deliberately no "reclaim"
	// concept on Pool itself (unlike structure.StructureBreak, which does
	// need one to distinguish BreakSweep from a held break). Whether a
	// sweep of this pool's level went on to become a structural sweep,
	// failed break, or held break is internal/structure's own
	// classification (BreakSweep/BreakFailed/BreakClose/BreakDisplacement),
	// cross-referenced by BrokenSwingID when the swept pool's Source is a
	// swing — not re-derived here.
	SweptAt *int64

	// ReclaimedAt is set when a close returns to the origin side of the
	// pool within the configured reclaim window after SweptAt — purely
	// informational context for a consumer (visualization, a future
	// strategy) asking "was this sweep reclaimed," never used to unset
	// SweptAt itself (see SweptAt's own doc comment on why sweeping a pool
	// needs no reclaim check to be a completed event).
	ReclaimedAt *int64

	Strength float64
}

// PoolFromSwing builds a single-swing liquidity pool — source task §29's
// "Swing High Liquidity"/"Swing Low Liquidity". The pool's price band is a
// tolerance-widened point (the swing price +/- toleranceATR*atr), not a
// bare price: liquidity never rests at one infinitely precise price in
// practice, and a widened band is what equal-level clustering (below)
// needs to compare against consistently.
func PoolFromSwing(s structure.Swing, atr, toleranceATR float64) Pool {
	side := LiquidityBuySide
	source := "swing_high"
	if s.Kind == structure.SwingLow {
		side = LiquiditySellSide
		source = "swing_low"
	}
	band := toleranceATR * atr
	return Pool{
		ID:           fmt.Sprintf("pool:%s:%s:%d", s.SourceTimeframe, source, s.Time),
		Side:         side,
		Low:          market.Price(float64(s.Price) - band),
		High:         market.Price(float64(s.Price) + band),
		Layer:        s.Layer,
		Source:       source,
		CreatedAt:    s.ConfirmedAt,
		TouchCount:   1,
		FirstTouchAt: s.ConfirmedAt,
		LastTouchAt:  s.ConfirmedAt,
		Strength:     s.Strength,
	}
}

// DetectSweep returns the time of the first candle (at or after fromIndex)
// that trades beyond pool's outer edge — source task §29's "Sweep". See
// Pool.SweptAt's doc comment for why this needs no separate reclaim check
// to BE a sweep; sweptIndex is returned alongside so DetectReclaim can
// resume scanning from exactly that point.
func DetectSweep(candles []market.Candle, fromIndex int, pool Pool) (sweptAt int64, sweptIndex int, found bool) {
	for i := fromIndex; i < len(candles); i++ {
		c := candles[i]
		if pool.Side == LiquidityBuySide && c.High > float64(pool.High) {
			return c.Time, i, true
		}
		if pool.Side == LiquiditySellSide && c.Low < float64(pool.Low) {
			return c.Time, i, true
		}
	}
	return 0, -1, false
}

// DetectReclaim reports the first close (within reclaimBars of
// sweptIndex) that returns to the pool's origin side — source task §29's
// "Reclaim," purely informational context on an already-swept pool (see
// Pool.ReclaimedAt).
func DetectReclaim(candles []market.Candle, sweptIndex, reclaimBars int, pool Pool) (int64, bool) {
	end := sweptIndex + reclaimBars
	if end > len(candles)-1 {
		end = len(candles) - 1
	}
	for i := sweptIndex + 1; i <= end; i++ {
		c := candles[i]
		if pool.Side == LiquidityBuySide && c.Close <= float64(pool.High) {
			return c.Time, true
		}
		if pool.Side == LiquiditySellSide && c.Close >= float64(pool.Low) {
			return c.Time, true
		}
	}
	return 0, false
}
