package zone

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// buildIFVGZones ports technique_geometry.py::discover_ifvg_instances
// ("T4 — first close through gap flips side"), locked in the Phase S2
// catalog (docs/analysis/strategy-v2-catalog.md #6): for each existing
// FVG zone, the inversion trigger is the first candle CLOSE fully
// through the gap's far bound (below Low for a demand-side gap, above
// High for a supply-side gap) — that flips the zone's tradeable side
// (demand->sell, supply->buy). The flip is confirmed only if no later
// close in the given causal window reverses it back through the
// ORIGINAL bound from the new trade side; if one does, the inversion
// never fires (this re-derives fresh every Update call, so a flip that
// was valid in an earlier window can legitimately stop existing once a
// later reversing bar enters the window — the same "pure, recomputed
// fresh" contract every other Update in this package already has).
// Entry remains the same gap band; clipping to a tradeable width is a
// strategy-layer concern, not this canonical geometry.
func buildIFVGZones(candles []market.Candle, fvgZones []Zone, tf market.Timeframe, atr float64, cfg Config) []Zone {
	var zones []Zone
	for _, z := range fvgZones {
		originIdx := indexOfTimeFrom(candles, 0, z.OriginTime)
		if originIdx < 0 {
			continue
		}
		low, high := float64(z.Low), float64(z.High)
		invertedSide := Side(0)
		invertIndex := -1
		found := false
		for i := originIdx + 1; i < len(candles); i++ {
			close := candles[i].Close
			if z.Side == Demand && close < low {
				invertedSide, invertIndex, found = Supply, i, true
				break
			}
			if z.Side == Supply && close > high {
				invertedSide, invertIndex, found = Demand, i, true
				break
			}
		}
		if !found {
			continue
		}
		reversed := false
		for i := invertIndex + 1; i < len(candles); i++ {
			close := candles[i].Close
			if invertedSide == Demand && close < low {
				reversed = true
				break
			}
			if invertedSide == Supply && close > high {
				reversed = true
				break
			}
		}
		if reversed {
			continue
		}
		origin := candles[invertIndex]
		boundary := low
		if invertedSide == Demand {
			boundary = high
		}
		zones = append(zones, Zone{
			ID:              fmt.Sprintf("zone:%s:%s:%d", tf, KindIFVG, origin.Time),
			Kind:            KindIFVG,
			Side:            invertedSide,
			Low:             market.Price(low),
			High:            market.Price(high),
			Timeframe:       tf,
			OriginTime:      origin.Time,
			DisplacementRef: z.ID,
			CreatedAt:       origin.Time,
			BreakIndex:      invertIndex,
			Strength:        inversionStrength(z, origin, boundary, atr),
		})
	}
	return zones
}
