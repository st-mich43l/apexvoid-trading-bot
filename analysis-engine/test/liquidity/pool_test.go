package liquidity_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func c(t int64, o, h, l, cl float64) market.Candle {
	return market.Candle{Time: t, Open: o, High: h, Low: l, Close: cl, Volume: 1}
}

func TestPoolFromSwing_BuySideForAHighSellSideForALow(t *testing.T) {
	high := structure.Swing{Kind: structure.SwingHigh, Price: 100, Layer: structure.StructureMajor, ConfirmedAt: 5, Strength: 2}
	pool := liquidity.PoolFromSwing(high, 1.0, 0.1)
	if pool.Side != liquidity.LiquidityBuySide {
		t.Errorf("a swing high is buy-side (resting) liquidity, got %v", pool.Side)
	}
	if pool.Low >= high.Price || pool.High <= high.Price {
		t.Errorf("the pool band must straddle the swing price, got %v-%v around %v", pool.Low, pool.High, high.Price)
	}
	if pool.Source != "swing_high" {
		t.Errorf("expected Source swing_high, got %q", pool.Source)
	}

	low := structure.Swing{Kind: structure.SwingLow, Price: 90, Layer: structure.StructureMicro, ConfirmedAt: 5, Strength: 1}
	poolLow := liquidity.PoolFromSwing(low, 1.0, 0.1)
	if poolLow.Side != liquidity.LiquiditySellSide {
		t.Errorf("a swing low is sell-side liquidity, got %v", poolLow.Side)
	}
}

func TestDetectSweepAndReclaim(t *testing.T) {
	pool := liquidity.Pool{Side: liquidity.LiquidityBuySide, Low: 99.5, High: 100.5}
	bars := []market.Candle{
		c(1, 99.8, 100.2, 99.6, 100.0),  // does not clear the pool's High (100.5)
		c(2, 100.0, 100.7, 99.9, 100.4), // sweeps it (High 100.7 > 100.5)
		c(3, 100.4, 100.6, 99.8, 100.0), // reclaims (Close 100.0 <= 100.5)
	}
	sweptAt, sweptIndex, found := liquidity.DetectSweep(bars, 0, pool)
	if !found {
		t.Fatal("expected a sweep")
	}
	if sweptAt != bars[1].Time || sweptIndex != 1 {
		t.Errorf("expected sweep at index 1 (time %d), got index %d (time %d)", bars[1].Time, sweptIndex, sweptAt)
	}
	reclaimedAt, ok := liquidity.DetectReclaim(bars, sweptIndex, 3, pool)
	if !ok || reclaimedAt != bars[2].Time {
		t.Errorf("expected reclaim at bar 2 (time %d), got ok=%v time=%d", bars[2].Time, ok, reclaimedAt)
	}
}

func TestClusterEqualLevels_RequiresMinimumTouches(t *testing.T) {
	mk := func(price float64, confirmedAt int64) structure.Swing {
		return structure.Swing{Kind: structure.SwingHigh, Layer: structure.StructureMajor, Price: market.Price(price), ExcursionPrice: 5, ExcursionATR: 1, ConfirmedAt: confirmedAt}
	}
	// Three touches within tolerance (atr=1, tol=0.1 -> band=0.1) at ~100.
	swings := []structure.Swing{mk(100.0, 1), mk(100.05, 2), mk(99.98, 3)}

	pools := liquidity.ClusterEqualLevels(swings, structure.SwingHigh, structure.StructureMajor, 1.0, 0.1, 3)
	if len(pools) != 1 {
		t.Fatalf("expected exactly 1 equal-high pool with 3 touches, got %d", len(pools))
	}
	if pools[0].TouchCount != 3 {
		t.Errorf("expected TouchCount 3, got %d", pools[0].TouchCount)
	}

	// Same data, minTouches=4: nothing clears the bar.
	none := liquidity.ClusterEqualLevels(swings, structure.SwingHigh, structure.StructureMajor, 1.0, 0.1, 4)
	if len(none) != 0 {
		t.Errorf("expected no pool when minTouches is not met, got %d", len(none))
	}
}

func TestClusterEqualLevels_OutsideToleranceStaysSeparate(t *testing.T) {
	mk := func(price float64, confirmedAt int64) structure.Swing {
		return structure.Swing{Kind: structure.SwingLow, Layer: structure.StructureMajor, Price: market.Price(price), ExcursionPrice: 5, ExcursionATR: 1, ConfirmedAt: confirmedAt}
	}
	// Two clusters, each with 2 touches, well outside each other's tolerance band.
	swings := []structure.Swing{mk(100.0, 1), mk(100.02, 2), mk(110.0, 3), mk(110.03, 4)}
	pools := liquidity.ClusterEqualLevels(swings, structure.SwingLow, structure.StructureMajor, 1.0, 0.1, 2)
	if len(pools) != 2 {
		t.Fatalf("expected 2 separate equal-low pools, got %d", len(pools))
	}
}
