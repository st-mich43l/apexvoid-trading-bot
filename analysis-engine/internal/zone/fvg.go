package zone

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// buildFVGZones ports zones.py::fvg(): the classic 3-candle fair value
// gap, comparing candle i-2 ("older") to candle i ("cur") for every i in
// [2, len(candles)) — candle i-1 is deliberately never checked, a pure
// 2-bar-apart high/low comparison, not a 3-candle overlap test.
func buildFVGZones(candles []market.Candle, tf market.Timeframe, atr float64, cfg Config) []Zone {
	var zones []Zone
	for i := 2; i < len(candles); i++ {
		older, cur := candles[i-2], candles[i]
		switch {
		case older.High < cur.Low:
			zones = append(zones, fvgZone(older.High, cur.Low, Demand, cur, tf, i, fvgStrength(candles, i, older.High, cur.Low, atr)))
		case older.Low > cur.High:
			zones = append(zones, fvgZone(cur.High, older.Low, Supply, cur, tf, i, fvgStrength(candles, i, cur.High, older.Low, atr)))
		}
	}
	return zones
}

func fvgZone(low, high float64, side Side, origin market.Candle, tf market.Timeframe, index int, strength float64) Zone {
	return Zone{
		ID:         fmt.Sprintf("zone:%s:%s:%d", tf, KindFVG, origin.Time),
		Kind:       KindFVG,
		Side:       side,
		Low:        market.Price(low),
		High:       market.Price(high),
		Timeframe:  tf,
		OriginTime: origin.Time,
		CreatedAt:  origin.Time,
		BreakIndex: index,
		Strength:   strength,
	}
}
