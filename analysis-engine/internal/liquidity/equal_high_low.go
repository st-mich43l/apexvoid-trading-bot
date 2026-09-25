package liquidity

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// ClusterEqualLevels groups same-kind, same-layer swings whose prices lie
// within an ATR-normalized tolerance of each other into equal-high/
// equal-low pools — source task §31: "do not use exact price equality...
// cluster using instrument-aware tolerance... track touch count, first
// touch, last touch, price band." Only clusters with at least minTouches
// members become a Pool — a lone swing is not "equal" anything by itself
// (PoolFromSwing covers the single-swing case).
//
// Clustering is chain-based (a candidate joins a cluster if it is within
// tolerance of ANY existing member, not just the first) — deliberate:
// three touches that each drift slightly from their immediate neighbor but
// stay within the same overall band are real, repeated liquidity at one
// level, not three unrelated levels. swings must already be filtered to
// one kind/layer by the caller, or pass the full set and let kind/layer
// here do the filtering — both work; this function filters internally so
// callers can pass structure.StructureState.Swings directly.
func ClusterEqualLevels(
	swings []structure.Swing, kind structure.SwingKind, layer structure.StructureLayer,
	atr, toleranceATR float64, minTouches int,
) []Pool {
	tol := toleranceATR * atr
	var candidates []structure.Swing
	for _, s := range swings {
		if s.Kind == kind && s.Layer == layer {
			candidates = append(candidates, s)
		}
	}

	used := make([]bool, len(candidates))
	var pools []Pool
	for i := range candidates {
		if used[i] {
			continue
		}
		cluster := []structure.Swing{candidates[i]}
		used[i] = true
		for j := i + 1; j < len(candidates); j++ {
			if used[j] {
				continue
			}
			if withinToleranceOfAny(candidates[j], cluster, tol) {
				cluster = append(cluster, candidates[j])
				used[j] = true
			}
		}
		if len(cluster) < minTouches {
			continue
		}
		pools = append(pools, poolFromCluster(cluster, kind, layer))
	}
	return pools
}

func withinToleranceOfAny(candidate structure.Swing, cluster []structure.Swing, tol float64) bool {
	for _, member := range cluster {
		if candidate.Price.Distance(member.Price) <= tol {
			return true
		}
	}
	return false
}

func poolFromCluster(cluster []structure.Swing, kind structure.SwingKind, layer structure.StructureLayer) Pool {
	low, high := cluster[0].Price, cluster[0].Price
	firstTouch, lastTouch := cluster[0].ConfirmedAt, cluster[0].ConfirmedAt
	var strengthSum float64
	for _, s := range cluster {
		if s.Price < low {
			low = s.Price
		}
		if s.Price > high {
			high = s.Price
		}
		if s.ConfirmedAt < firstTouch {
			firstTouch = s.ConfirmedAt
		}
		if s.ConfirmedAt > lastTouch {
			lastTouch = s.ConfirmedAt
		}
		strengthSum += s.Strength
	}
	side := LiquidityBuySide
	source := "equal_high"
	if kind == structure.SwingLow {
		side = LiquiditySellSide
		source = "equal_low"
	}
	// More touches at the same level means more resting liquidity has
	// accumulated there, not less — the cluster's average strength is
	// scaled by touch count, deliberately not averaged away.
	strength := (strengthSum / float64(len(cluster))) * float64(len(cluster))
	return Pool{
		ID:           fmt.Sprintf("pool:%s:%.5f:%d", source, float64(low), firstTouch),
		Side:         side,
		Low:          low,
		High:         high,
		Layer:        layer,
		Source:       source,
		CreatedAt:    firstTouch,
		TouchCount:   len(cluster),
		FirstTouchAt: firstTouch,
		LastTouchAt:  lastTouch,
		Strength:     strength,
	}
}
