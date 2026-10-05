// Package legacyfixture builds market contexts with a hand-set detector
// contract frame so strategy tests control exactly the facts a frozen
// detector reads (swings, zones, grabs, levels, ranges, bias) without running
// the whole analysis pipeline. It is test support only.
package legacyfixture

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Params merges a strategy's own parameters with the shared frozen-detector
// contract the engine injects in production (XAU instrument scale).
func Params(own map[string]any) map[string]any {
	params := map[string]any{
		"maximum_entry_atr": 2.0, "maximum_zone_width_atr": 1.5, "proximal_band_atr": .5, "confluence_floor": 2.0,
		"fibonacci_enabled": true, "reaction_lookback_bars": 3.0, "engulfing_minimum_range_atr": .5,
		"pip_size": .1, "fvg_entry_max_width_price": 5.0, "confluence_scoring_version": "v1",
		"confluence_star_three_ratio": .585, "confluence_star_two_ratio": .39, "confluence_zone_quality_weight": 4.0,
		"confluence_mad_score_weight": 2.0, "fibonacci_confluence_weight": 2.5, "fibonacci_epsilon_atr": .15,
	}
	for key, value := range own {
		params[key] = value
	}
	return params
}

// Context builds an XAU context whose primary timeframe carries bars and a
// legacy frame with the given detector ATR. The read defaults to an up
// structure with an up higher-timeframe bias and counter-trend allowed;
// mutate may change the frame and the read.
func Context(bars []market.Candle, detectorATR float64, mutate func(*analysiscontext.LegacyFrame, *analysiscontext.LegacyRead)) *analysiscontext.MarketContext {
	frame := &analysiscontext.LegacyFrame{
		Bars: bars, DetectorATR: detectorATR, Structure: "up", Regime: regime.State{Kind: "trend"}, Momentum: momentum.Neutral,
		Compat: techniquezone.CompatConfig{PipSize: .1, DisplacementBodyFraction: .55, DisplacementATRMult: 1.5, FractalN: 2, ATRLength: 14, LevelClusterATR: .5, RoundStep: 5, MaximumClusterSpanMultiple: 2},
	}
	read := analysiscontext.LegacyRead{LocalStructure: "up", HTFBias: "up", AllowCounterTrend: true}
	if mutate != nil {
		mutate(frame, &read)
	}
	return &analysiscontext.MarketContext{
		Symbol: "XAU", Volatility: analysiscontext.VolatilityContext{ATR: detectorATR}, Legacy: &read,
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5: {Timeframe: market.M5, Candles: bars, Regime: frame.Regime, Legacy: frame},
		},
	}
}

// Bars builds n quiet closed bars around price, one per five minutes.
func Bars(n int, price float64) []market.Candle {
	bars := make([]market.Candle, n)
	for i := range bars {
		bars[i] = market.Candle{Time: int64(i+1) * 300, Open: price, High: price + .5, Low: price - .5, Close: price}
	}
	return bars
}
