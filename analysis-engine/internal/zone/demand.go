package zone

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// buildDemandZones ports zones.py::supply_demand()'s demand-side output
// (a bullish displacement run's own base): base candles are up to 3
// candles immediately before the run's start; the zone's [low,high] is
// the base's own low/high; origin is the base's last candle
// (run.StartIndex-1). BreakIndex is the run's OWN END (not
// origin+1/start+1) — the documented fix: the run's first bar wicks back
// into its own base by construction, so scanning mitigation from
// anywhere inside the run itself falsely self-mitigates the zone on its
// formation bar.
func buildDemandZones(candles []market.Candle, runs []displacementRun, tf market.Timeframe, atr float64, cfg Config) []Zone {
	var zones []Zone
	for _, run := range runs {
		if run.Direction != market.Buy || run.StartIndex <= 0 {
			continue
		}
		zones = append(zones, zoneFromRun(candles, run, Demand, KindDemand, tf, atr))
	}
	return zones
}

// zoneFromRun is the shared supply/demand construction — the two sides
// differ only in which Side/Kind they produce and which direction the
// run must be, per zones.py::supply_demand.
func zoneFromRun(candles []market.Candle, run displacementRun, side Side, kind Kind, tf market.Timeframe, atr float64) Zone {
	baseStart := run.StartIndex - 3
	if baseStart < 0 {
		baseStart = 0
	}
	base := candles[baseStart:run.StartIndex]
	low, high := base[0].Low, base[0].High
	for _, c := range base[1:] {
		if c.Low < low {
			low = c.Low
		}
		if c.High > high {
			high = c.High
		}
	}
	origin := candles[run.StartIndex-1]
	return Zone{
		ID:              fmt.Sprintf("zone:%s:%s:%d", tf, kind, origin.Time),
		Kind:            kind,
		Side:            side,
		Low:             market.Price(low),
		High:            market.Price(high),
		Timeframe:       tf,
		OriginTime:      origin.Time,
		DisplacementRef: fmt.Sprintf("displacement:%s:%d:%d", tf, run.StartTime, run.EndTime),
		CreatedAt:       origin.Time,
		BreakIndex:      run.EndIndex,
		Strength:        run.Strength,
	}
}
