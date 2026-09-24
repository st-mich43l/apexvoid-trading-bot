package zone

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// buildBreakerZones ports zones.py::breaker_blocks(): an Order Block
// whose structural edge is violated by a CLOSING price (never a wick —
// demand zone: close < Low; supply zone: close > High) becomes a new
// zone on the OPPOSITE side, same [Low,High] band, originating at the
// violation bar. The original Order Block is left in the returned OB
// slice unchanged (its own lifecycle state will naturally show
// Invalidated once mitigation scanning reaches this same violation
// close, per lifecycle.go) — this function only adds the new breaker
// zone, it does not mutate obZones.
func buildBreakerZones(candles []market.Candle, obZones []Zone, tf market.Timeframe, atr float64, cfg Config) []Zone {
	var zones []Zone
	for _, ob := range obZones {
		originIdx := indexOfTimeFrom(candles, 0, ob.OriginTime)
		if originIdx < 0 {
			continue
		}
		low, high := float64(ob.Low), float64(ob.High)
		violatedAt := -1
		for i := originIdx + 1; i < len(candles); i++ {
			c := candles[i]
			if ob.Side == Demand && c.Close < low {
				violatedAt = i
				break
			}
			if ob.Side == Supply && c.Close > high {
				violatedAt = i
				break
			}
		}
		if violatedAt < 0 {
			continue
		}
		flippedSide := Supply
		if ob.Side == Supply {
			flippedSide = Demand
		}
		origin := candles[violatedAt]
		boundary := low
		if flippedSide == Demand {
			boundary = high
		}
		zones = append(zones, Zone{
			ID:              fmt.Sprintf("zone:%s:%s:%d", tf, KindBreaker, origin.Time),
			Kind:            KindBreaker,
			Side:            flippedSide,
			Low:             ob.Low,
			High:            ob.High,
			Timeframe:       tf,
			OriginTime:      origin.Time,
			StructureRef:    ob.StructureRef,
			DisplacementRef: ob.ID,
			CreatedAt:       origin.Time,
			BreakIndex:      violatedAt,
			Strength:        inversionStrength(ob, origin, boundary, atr),
		})
	}
	return zones
}
