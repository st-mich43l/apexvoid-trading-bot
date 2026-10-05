package techniquezone

import (
	"math"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

func TestLiquidityPoolsClusterEqualSwingsAndKeepTheLoneExtreme(t *testing.T) {
	bars := make([]market.Candle, 30)
	for i := range bars {
		bars[i] = bar(i, 100, 101, 99, 100) // ATR is constant 2
	}
	atr := ATRSeries(bars, 14)
	swings := []Swing{
		{Index: 3, Kind: "high", Price: 104.00}, {Index: 8, Kind: "high", Price: 104.10},
		{Index: 12, Kind: "high", Price: 108.00}, // lone extreme, far from the cluster
		{Index: 5, Kind: "low", Price: 95.00}, {Index: 15, Kind: "low", Price: 95.05},
	}
	pools := LiquidityPools(swings, bars, 0.15, atr, 2)
	band := 2 * 0.15
	var buyCluster, sellCluster, lone *Pool
	for i := range pools {
		switch {
		case pools[i].Side == "buy" && pools[i].Touches == 2:
			buyCluster = &pools[i]
		case pools[i].Side == "sell" && pools[i].Touches == 2:
			sellCluster = &pools[i]
		case pools[i].Side == "buy" && pools[i].Touches == 1:
			lone = &pools[i]
		}
	}
	if buyCluster == nil || math.Abs(buyCluster.Level-104.05) > 1e-9 || buyCluster.Band != band {
		t.Fatalf("equal-high cluster wrong: %+v", pools)
	}
	if sellCluster == nil || math.Abs(sellCluster.Level-95.025) > 1e-9 {
		t.Fatalf("equal-low cluster wrong: %+v", pools)
	}
	if lone == nil || lone.Level != 108 {
		t.Fatalf("the highest swing high is kept as a single-touch pool: %+v", pools)
	}
	for i := 1; i < len(pools); i++ {
		if pools[i-1].Level > pools[i].Level {
			t.Fatalf("pools are ordered by level: %+v", pools)
		}
	}
	if LiquidityPools(nil, bars, 0.15, atr, 2) != nil {
		t.Fatal("no swings, no pools")
	}
}

func TestLiquidityGrabGradesFollowTheCloseAndTheReaction(t *testing.T) {
	pool := Pool{Side: "buy", Level: 104, Band: .3, Touches: 3}
	mk := func(open, high, low, closePrice float64) []market.Candle {
		bars := make([]market.Candle, 6)
		for i := range bars {
			bars[i] = bar(i, 100, 101, 99, 100)
		}
		bars[3] = bar(3, open, high, low, closePrice)
		return bars
	}
	legs := []Leg{{Start: 4, End: 5, Direction: "down", Size: 3}}
	atr := ATRSeries(mk(100, 101, 99, 100), 14)
	grab := func(bars []market.Candle, legs []Leg) *Grab {
		for _, g := range LiquidityGrabs(bars, []Pool{pool}, legs, nil, atr, .5, 3, .3, .1) {
			if g.Index == 3 {
				return &g
			}
		}
		return nil
	}
	// Clean close back below the level with a strong body and a following
	// displacement leg is A; without the leg it is B.
	strong := mk(104.4, 104.6, 102.6, 102.8)
	if g := grab(strong, legs); g == nil || g.Grade != "A" || g.Direction != "bear" || !g.ReactionDisplacement {
		t.Fatalf("expected an A bear grab, got %+v", g)
	}
	if g := grab(strong, nil); g == nil || g.Grade != "B" {
		t.Fatalf("expected a B grab without a displacement leg, got %+v", g)
	}
	// A close just above the level within 10% of the bar's range is marginal (C).
	marginal := mk(104.1, 104.8, 103.9, 104.05)
	if g := grab(marginal, nil); g == nil || g.Grade != "C" {
		t.Fatalf("expected a C grab, got %+v", g)
	}
	// A close far above the level is a break, not a grab.
	if g := grab(mk(104.2, 105.5, 104.1, 105.4), nil); g != nil {
		t.Fatalf("a close far above the pool is not a grab: %+v", g)
	}
	// The sweep must clear the pool band.
	if g := grab(mk(103.5, 104.2, 103.4, 103.6), nil); g != nil {
		t.Fatalf("a high inside the band is not a sweep: %+v", g)
	}
}

func TestInducementNeedsATwoTouchPoolInsideAZone(t *testing.T) {
	bars := make([]market.Candle, 20)
	for i := range bars {
		bars[i] = bar(i, 100, 101, 99, 100)
	}
	atr := ATRSeries(bars, 14)
	zones := []Zone{newZone(94, 96, "demand", 2, "supply_demand")}
	two := Pool{Side: "sell", Level: 95, Band: .3, Touches: 2}
	if !isInducement(two, zones, atr, .3, .1) {
		t.Fatal("a two-touch sell pool inside a demand zone is inducement")
	}
	if isInducement(Pool{Side: "sell", Level: 95, Band: .3, Touches: 3}, zones, atr, .3, .1) {
		t.Fatal("only exactly two touches can be inducement")
	}
	if isInducement(Pool{Side: "sell", Level: 95, Band: 1.5, Touches: 2}, zones, atr, .3, .1) {
		t.Fatal("a pool wider than the inducement band is not inducement")
	}
	if isInducement(Pool{Side: "sell", Level: 90, Band: .3, Touches: 2}, zones, atr, .3, .1) {
		t.Fatal("a pool away from the zone is not inducement")
	}
}
