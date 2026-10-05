package strategy_test

import (
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

func strategyContext(direction market.Direction, candles []market.Candle) *analysiscontext.MarketContext {
	return &analysiscontext.MarketContext{
		Symbol: "XAU", Bias: analysiscontext.BiasContext{Direction: direction}, Volatility: analysiscontext.VolatilityContext{ATR: 1},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{market.M5: {Timeframe: market.M5, Candles: candles, Regime: regime.State{Kind: "trend"}}},
	}
}

func TestZoneQualityScoreWinsAnchorSelection(t *testing.T) {
	ctx := strategyContext(market.Buy, []market.Candle{{Time: 1000, Open: 99, High: 101, Low: 98, Close: 100}})
	ctx.Timeframes[market.M5].Zones.Zones = []zone.Zone{
		{ID: "low-quality", Side: zone.Demand, Low: 99, High: 100, State: zone.StateFresh, Strength: 99, LegacyScore: 1},
		{ID: "high-quality", Side: zone.Demand, Low: 99.2, High: 100.2, State: zone.StateFresh, Strength: .1, LegacyScore: 5},
	}
	zones := strategyutil.LiveZones(ctx.Timeframes[market.M5], market.Buy, 100, 1, 2)
	if len(zones) != 2 || zones[0].ID != "high-quality" {
		t.Fatalf("zone quality score must select the strong anchor first: %+v", zones)
	}
}
