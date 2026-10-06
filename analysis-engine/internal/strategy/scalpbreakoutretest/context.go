package scalpbreakoutretest

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// scalpContext is the shared scalp context plus the setup window's key levels.
type scalpContext struct {
	strategyutil.ScalpContext
	KeyLevels []keyLevel
}

// buildContext reproduces the snapshot the Python lane held when it evaluated the
// closed M1 bar `now` (see strategyutil.BuildScalpContext) and adds the setup
// window's key levels, which scalp_structure() computed from the same M5 window.
func buildContext(cfg Config, m5 []market.Candle, m1All []market.Candle, now int64) (scalpContext, bool) {
	base, ok := strategyutil.BuildScalpContext(strategyutil.ScalpContextConfig{
		PipSize: cfg.PipSize, ContextMaxAgeSeconds: cfg.ContextMaxAgeSeconds, ContextATRBars: cfg.ContextATRBars,
		ActiveRangeBars: cfg.ActiveRangeBars, ConfirmationWindowBars: cfg.ConfirmationWindowBars,
	}, m5, m1All, now)
	if !ok {
		return scalpContext{}, false
	}
	c := scalpContext{ScalpContext: base}
	atr := techniquezone.ATRSeries(m5, cfg.ContextATRBars)
	swings := techniquezone.FindSwings(m5, cfg.SwingFractalN, 0, 1, atr, -1)
	for _, level := range techniquezone.KeyLevels(swings, atr, cfg.LevelClusterATR, cfg.LevelRoundStep, cfg.LevelMinimumTouches, cfg.LevelMaxClusterSpan, nil) {
		c.KeyLevels = append(c.KeyLevels, keyLevel{Price: level.Price, Touches: level.Touches})
	}
	return c, true
}
