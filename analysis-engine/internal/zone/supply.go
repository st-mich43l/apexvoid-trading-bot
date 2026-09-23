package zone

import "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"

// buildSupplyZones is buildDemandZones' mirror for bearish displacement
// runs — see zoneFromRun (demand.go) for the shared construction and
// zones.py::supply_demand for the ported source logic.
func buildSupplyZones(candles []market.Candle, runs []displacementRun, tf market.Timeframe, atr float64, cfg Config) []Zone {
	var zones []Zone
	for _, run := range runs {
		if run.Direction != market.Sell || run.StartIndex <= 0 {
			continue
		}
		zones = append(zones, zoneFromRun(candles, run, Supply, KindSupply, tf, atr))
	}
	return zones
}
