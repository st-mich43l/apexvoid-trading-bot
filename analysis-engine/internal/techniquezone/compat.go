package techniquezone

import (
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The helpers below port the compatibility layer of structure.py that the
// frozen detectors call with their own default arguments (find_retest and
// entry_zone). Their defaults are intentionally preserved: the detectors
// observed exactly these values, including the default pip size of 0.1 used by
// the compat flip-zone retest.

// CompatConfig carries the default arguments the frozen detectors' compat
// helpers ran with. They are deliberately the helpers' own defaults, not the
// instrument's: the oracle observed them, including the 0.1 pip size and the
// 5.0 round step the compat flip zones use on every instrument.
type CompatConfig struct {
	PipSize                    float64
	DisplacementBodyFraction   float64
	DisplacementATRMult        float64
	FractalN                   int
	ATRLength                  int
	LevelClusterATR            float64
	RoundStep                  float64
	MaximumClusterSpanMultiple float64
}

// compatTolerance mirrors structure._tol.
func compatTolerance(bars []market.Candle, pipSize float64) float64 {
	if len(bars) == 0 {
		return 0
	}
	low, high := bars[0].Low, bars[0].High
	for _, bar := range bars[1:] {
		low, high = math.Min(low, bar.Low), math.Max(high, bar.High)
	}
	return math.Max((high-low)*0.003, pipSize)
}

func lastConsecutiveBreakIndex(bars []market.Candle, price float64, required int, above bool) int {
	run, last := 0, -1
	for i, bar := range bars {
		beyond := bar.Close < price
		if above {
			beyond = bar.Close > price
		}
		if beyond {
			run++
			if run == required {
				last = i
			}
		} else {
			run = 0
		}
	}
	return last
}

// FindRetest mirrors structure.find_retest: the first bar after the latest
// completed break of price (minConsecutiveCloses consecutive closes beyond it)
// that touches the level and closes back on the broken side.
func FindRetest(bars []market.Candle, price float64, minConsecutiveCloses int, pipSize float64) (Zone, bool) {
	if len(bars) < 3 {
		return Zone{}, false
	}
	tolerance := compatTolerance(bars, pipSize)
	required := maxInt(1, minConsecutiveCloses)
	up := lastConsecutiveBreakIndex(bars, price, required, true)
	down := lastConsecutiveBreakIndex(bars, price, required, false)
	breakIndex, direction := -1, ""
	if up >= 0 && (down < 0 || up > down) {
		breakIndex, direction = up, "buy"
	} else if down >= 0 {
		breakIndex, direction = down, "sell"
	}
	if breakIndex < 0 {
		return Zone{}, false
	}
	for i := breakIndex + 1; i < len(bars); i++ {
		bar := bars[i]
		if !(bar.Low-tolerance <= price && price <= bar.High+tolerance) {
			continue
		}
		if direction == "buy" && bar.Close >= price {
			return newZone(price-tolerance, price+tolerance, "demand", i, "retest_support"), true
		}
		if direction == "sell" && bar.Close <= price {
			return newZone(price-tolerance, price+tolerance, "supply", i, "retest_resistance"), true
		}
	}
	return Zone{}, false
}

// EntryZone mirrors structure.entry_zone: the latest compat order-block, flip
// or FVG zone whose tolerance-widened band contains price, else a band of the
// compat tolerance around it.
func EntryZone(bars []market.Candle, price float64, direction string, pipSize float64, compat CompatConfig) Zone {
	tolerance := compatTolerance(bars, pipSize)
	zones := compatOrderBlocks(bars, compat)
	zones = append(zones, compatFlipZones(bars, compat)...)
	zones = append(zones, FVG(bars)...)
	for i := len(zones) - 1; i >= 0; i-- {
		zone := zones[i]
		if zone.Low()-tolerance <= price && price <= zone.High()+tolerance {
			return zone
		}
	}
	side := "supply"
	if direction == "BUY" {
		side = "demand"
	}
	return newZone(price-tolerance, price+tolerance, side, -1, side)
}

// compatOrderBlocks mirrors structure.order_blocks(df) with its defaults.
func compatOrderBlocks(bars []market.Candle, compat CompatConfig) []Zone {
	atr := ATRSeries(bars, compat.ATRLength)
	pivots := FindSwings(bars, compat.FractalN, 0, 0, atr, -1)
	breaks := StructureBreaks(pivots, bars, false, compat.FractalN)
	legs := Displacement(bars, atr, compat.DisplacementATRMult, compat.DisplacementBodyFraction)
	zones := OrderBlocks(bars, legs, breaks, "body")
	if len(zones) > 0 {
		return MarkMitigation(zones, bars, len(bars))
	}
	return legacyOrderBlocks(bars)
}

// legacyOrderBlocks mirrors structure._legacy_order_blocks.
func legacyOrderBlocks(bars []market.Candle) []Zone {
	var zones []Zone
	for i := 1; i < len(bars); i++ {
		prev, cur := bars[i-1], bars[i]
		lo := maxInt(0, i-9)
		sum := 0.0
		for j := lo; j <= i; j++ {
			sum += bars[j].High - bars[j].Low
		}
		average := sum / float64(i-lo+1)
		if math.Abs(cur.Close-cur.Open) < average {
			continue
		}
		if cur.Close > cur.Open && prev.Close < prev.Open {
			zones = append(zones, newZone(prev.Low, prev.High, "demand", i-1, "bullish_ob"))
		}
		if cur.Close < cur.Open && prev.Close > prev.Open {
			zones = append(zones, newZone(prev.Low, prev.High, "supply", i-1, "bearish_ob"))
		}
	}
	return zones
}

// compatFlipZones mirrors structure.flip_zones(df): a default find_retest of
// every compat key level.
func compatFlipZones(bars []market.Candle, compat CompatConfig) []Zone {
	atr := ATRSeries(bars, compat.ATRLength)
	swings := FindSwings(bars, compat.FractalN, 0, 0, atr, -1)
	levels := KeyLevels(swings, atr, compat.LevelClusterATR, compat.RoundStep, 1, compat.MaximumClusterSpanMultiple, nil)
	var zones []Zone
	for _, level := range levels {
		if zone, ok := FindRetest(bars, level.Price, 1, compat.PipSize); ok {
			zones = append(zones, zone)
		}
	}
	return zones
}

// DetectorATR mirrors indicators.atr -> pandas_ta.atr(length) as the frozen
// detectors read it (_atr -> _last): the true range seeded with the mean of
// its first length values and then smoothed with Wilder's recursion
// (alpha = 1/length). It returns the latest finite value, or fallback when the
// series is not warm.
func DetectorATR(bars []market.Candle, length int, fallback float64) float64 {
	if length < 1 || len(bars) < length {
		return fallback
	}
	tr := make([]float64, len(bars))
	for i, bar := range bars {
		r := bar.High - bar.Low
		if i > 0 {
			prev := bars[i-1].Close
			r = math.Max(r, math.Max(math.Abs(bar.High-prev), math.Abs(bar.Low-prev)))
		}
		tr[i] = r
	}
	sum := 0.0
	for _, v := range tr[:length] {
		sum += v
	}
	value := sum / float64(length)
	alpha := 1 / float64(length)
	for i := length; i < len(tr); i++ {
		value = (1-alpha)*value + alpha*tr[i]
	}
	if math.IsNaN(value) || math.IsInf(value, 0) || value <= 0 {
		return fallback
	}
	return value
}
