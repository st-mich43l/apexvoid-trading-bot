package liquidity

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// LiquidityState is the complete liquidity read for one symbol+timeframe
// at one point in time — the liquidity analogue of structure.StructureState.
type LiquidityState struct {
	Pools []Pool
}

var allLayers = [...]structure.StructureLayer{
	structure.StructureMicro, structure.StructureInternal,
	structure.StructureIntermediate, structure.StructureMajor,
}
var bothKinds = [...]structure.SwingKind{structure.SwingHigh, structure.SwingLow}

// Update is Structure V2's liquidity counterpart: one pool per confirmed
// swing (Swing High/Low Liquidity), plus equal-level clusters per
// layer+kind, each checked for a sweep (and, if swept, an informational
// reclaim) against the given candles. Pure and causal, same contract as
// structure.Update: only ever reads candles/atrSeries/swings given to it.
//
// swings should be structure.StructureState.Swings from the SAME
// candles/atrSeries pass — liquidity never recomputes swings itself
// (source task §2's core redesign principle, restated at this boundary).
func Update(candles []market.Candle, atrSeries []float64, swings []structure.Swing, cfg Config) LiquidityState {
	currentATR := lastValue(atrSeries)

	pools := make([]Pool, 0, len(swings))
	for _, s := range swings {
		pools = append(pools, PoolFromSwing(s, currentATR, cfg.EqualLevelToleranceATR))
	}
	for _, layer := range allLayers {
		for _, kind := range bothKinds {
			pools = append(pools, ClusterEqualLevels(
				swings, kind, layer, currentATR, cfg.EqualLevelToleranceATR, cfg.PoolMinimumTouches,
			)...)
		}
	}

	for i := range pools {
		fromIndex := indexAtOrAfter(candles, pools[i].CreatedAt)
		if fromIndex < 0 {
			continue
		}
		sweptAt, sweptIndex, found := DetectSweep(candles, fromIndex, pools[i])
		if !found {
			continue
		}
		t := sweptAt
		pools[i].SweptAt = &t
		if reclaimedAt, ok := DetectReclaim(candles, sweptIndex, cfg.SweepReclaimBars, pools[i]); ok {
			r := reclaimedAt
			pools[i].ReclaimedAt = &r
		}
	}
	return LiquidityState{Pools: pools}
}

func lastValue(series []float64) float64 {
	if len(series) == 0 {
		return 0
	}
	return series[len(series)-1]
}

// indexAtOrAfter mirrors structure package's own unexported helper of the
// same name — small enough (binary search over a Time-ascending slice)
// that duplicating it here is clearer than exporting internal plumbing
// from structure just for this one call site.
func indexAtOrAfter(candles []market.Candle, t int64) int {
	lo, hi := 0, len(candles)
	for lo < hi {
		mid := (lo + hi) / 2
		if candles[mid].Time < t {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	if lo == len(candles) {
		return -1
	}
	return lo
}
