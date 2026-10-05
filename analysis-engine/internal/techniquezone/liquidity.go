package techniquezone

import (
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Pool mirrors types.Pool: a cluster of equal swing highs ("buy" side
// liquidity) or lows ("sell" side liquidity).
type Pool struct {
	Side    string // "buy" | "sell"
	Level   float64
	Band    float64
	Touches int
}

// Grab mirrors types.Grab: a wick through a pool with the grade of the close
// back.
type Grab struct {
	Index                int
	Direction            string // "bull" | "bear"
	Grade                string // "A" | "B" | "C"
	Pool                 Pool
	ReactionDisplacement bool
	Inducement           bool
}

// LiquidityPools mirrors liquidity.liquidity_pools.
func LiquidityPools(swings []Swing, bars []market.Candle, equalTolATR float64, atr []float64, maxClusterSpanMultiple float64) []Pool {
	if len(swings) == 0 {
		return nil
	}
	if atr == nil {
		atr = ATRSeries(bars, 14)
	}
	band := ATRScalar(atr, 1) * math.Max(0, equalTolATR)
	var highs, lows []Swing
	for _, swing := range swings {
		switch swing.Kind {
		case "high":
			highs = append(highs, swing)
		case "low":
			lows = append(lows, swing)
		}
	}
	pools := append(clusterPools(highs, "buy", band, maxClusterSpanMultiple), clusterPools(lows, "sell", band, maxClusterSpanMultiple)...)
	if len(highs) > 0 {
		extreme := highs[0]
		for _, swing := range highs[1:] {
			if swing.Price > extreme.Price {
				extreme = swing
			}
		}
		pools = appendLoneExtreme(pools, Pool{Side: "buy", Level: extreme.Price, Band: band, Touches: 1})
	}
	if len(lows) > 0 {
		extreme := lows[0]
		for _, swing := range lows[1:] {
			if swing.Price < extreme.Price {
				extreme = swing
			}
		}
		pools = appendLoneExtreme(pools, Pool{Side: "sell", Level: extreme.Price, Band: band, Touches: 1})
	}
	sort.SliceStable(pools, func(i, j int) bool {
		if pools[i].Level != pools[j].Level {
			return pools[i].Level < pools[j].Level
		}
		return pools[i].Side < pools[j].Side
	})
	return pools
}

func clusterPools(swings []Swing, side string, band, maxClusterSpanMultiple float64) []Pool {
	var pools []Pool
	for _, cluster := range priceClusters(swings, band, maxClusterSpanMultiple) {
		if len(cluster) < 2 {
			continue
		}
		prices := make([]float64, len(cluster))
		for i, swing := range cluster {
			prices[i] = swing.Price
		}
		pools = append(pools, Pool{Side: side, Level: pySum(prices...) / float64(len(cluster)), Band: band, Touches: len(cluster)})
	}
	return pools
}

func appendLoneExtreme(pools []Pool, pool Pool) []Pool {
	for _, existing := range pools {
		if math.Abs(existing.Level-pool.Level) <= math.Max(existing.Band, pool.Band) {
			return pools
		}
	}
	return append(pools, pool)
}

// LiquidityGrabs mirrors liquidity.liquidity_grabs.
func LiquidityGrabs(bars []market.Candle, pools []Pool, legs []Leg, zones []Zone, atr []float64, sweepBodyFrac float64, sweepReactBars int, inducementBandATR, pipSize float64) []Grab {
	if atr == nil {
		atr = ATRSeries(bars, 14)
	}
	var grabs []Grab
	for i, bar := range bars {
		for _, pool := range pools {
			tolerance := math.Max(pool.Band, 0)
			switch {
			case pool.Side == "buy" && bar.High > pool.Level+tolerance:
				if grade := grabGrade(bars, i, pool.Level, "bear", legs, sweepBodyFrac, sweepReactBars); grade != "" {
					grabs = append(grabs, Grab{Index: i, Direction: "bear", Grade: grade, Pool: pool, ReactionDisplacement: hasReactionDisplacement(i, "bear", legs, sweepReactBars), Inducement: isInducement(pool, zones, atr, inducementBandATR, pipSize)})
				}
			case pool.Side == "sell" && bar.Low < pool.Level-tolerance:
				if grade := grabGrade(bars, i, pool.Level, "bull", legs, sweepBodyFrac, sweepReactBars); grade != "" {
					grabs = append(grabs, Grab{Index: i, Direction: "bull", Grade: grade, Pool: pool, ReactionDisplacement: hasReactionDisplacement(i, "bull", legs, sweepReactBars), Inducement: isInducement(pool, zones, atr, inducementBandATR, pipSize)})
				}
			}
		}
	}
	return grabs
}

func grabGrade(bars []market.Candle, index int, level float64, direction string, legs []Leg, sweepBodyFrac float64, sweepReactBars int) string {
	row := bars[index]
	tolerance := math.Max(row.High-row.Low, 0) * 0.1
	var clean, marginal bool
	if direction == "bear" {
		clean, marginal = row.Close < level, row.Close <= level+tolerance
	} else {
		clean, marginal = row.Close > level, row.Close >= level-tolerance
	}
	if clean {
		if bodyFraction(row) >= sweepBodyFrac && hasReactionDisplacement(index, direction, legs, sweepReactBars) {
			return "A"
		}
		return "B"
	}
	if marginal {
		return "C"
	}
	return ""
}

func hasReactionDisplacement(index int, direction string, legs []Leg, sweepReactBars int) bool {
	wanted := "down"
	if direction == "bull" {
		wanted = "up"
	}
	window := sweepReactBars
	if window < 0 {
		window = 0
	}
	for _, leg := range legs {
		if leg.Direction != wanted {
			continue
		}
		if delta := leg.Start - index; delta >= 0 && delta <= window {
			return true
		}
	}
	return false
}

func isInducement(pool Pool, zones []Zone, atr []float64, inducementBandATR, pipSize float64) bool {
	if pool.Touches != 2 {
		return false
	}
	if pool.Band > ATRScalar(atr, 1)*math.Max(0, inducementBandATR) {
		return false
	}
	for _, zone := range zones {
		width := math.Max(zone.High()-zone.Low(), 0)
		tolerance := math.Max(width, pipSize)
		if zone.Side == "demand" && pool.Side == "sell" && zone.Low()-tolerance <= pool.Level && pool.Level <= zone.High() {
			return true
		}
		if zone.Side == "supply" && pool.Side == "buy" && zone.Low() <= pool.Level && pool.Level <= zone.High()+tolerance {
			return true
		}
	}
	return false
}
