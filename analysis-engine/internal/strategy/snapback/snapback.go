// Package snapback implements extension mean reversion from the nearest
// canonical zone or key level. It does not require a liquidity sweep.
package snapback

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "snap_back"
const Version = "v2"

type Strategy struct {
	extensionATR, invalidationATR, targetFraction, expiryHours float64
	fingerprint                                                string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("snapback: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	vals := []*float64{&s.extensionATR, &s.invalidationATR, &s.targetFraction, &s.expiryHours}
	keys := []string{"extension_atr", "invalidation_buffer_atr", "target_fraction", "expiry_hours"}
	for i, k := range keys {
		v, e := strategyutil.Float(cfg.Parameters, k)
		if e != nil {
			return nil, e
		}
		*vals[i] = v
	}
	if s.extensionATR <= 0 || s.invalidationATR <= 0 || s.targetFraction <= 0 || s.targetFraction > 1 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("snapback: invalid parameters")
	}
	return s, nil
}
func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	bar, ok := strategyutil.LastBar(ctx, market.M5)
	atr := ctx.Volatility.ATR
	if tf == nil || !ok || atr <= 0 {
		return nil
	}
	anchor := 0.0
	anchorID := ""
	best := math.Inf(1)
	for _, level := range tf.KeyLevel.Levels {
		d := math.Abs(bar.Close - float64(level.Price))
		if d < best {
			best, anchor, anchorID = d, float64(level.Price), level.ID
		}
	}
	if anchorID == "" || best < s.extensionATR*atr {
		return nil
	}
	direction := market.Sell
	if bar.Close < anchor {
		direction = market.Buy
	}
	if direction == market.Buy && !bar.IsBullish() {
		return nil
	}
	if direction == market.Sell && !bar.IsBearish() {
		return nil
	}
	invalid := bar.Low - s.invalidationATR*atr
	if direction == market.Sell {
		invalid = bar.High + s.invalidationATR*atr
	}
	target := bar.Close + (anchor-bar.Close)*s.targetFraction
	q := strategyutil.Clamp01(best/(s.extensionATR*atr) - 1)
	episodeStart := extensionEpisodeStart(tf.Candles, anchor, s.extensionATR*atr)
	c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("snap:%s:%d", anchorID, episodeStart), Symbol: ctx.Symbol, Direction: direction, EntryLow: math.Min(bar.Open, bar.Close), EntryHigh: math.Max(bar.Open, bar.Close), Invalidation: invalid, InvalidationLabel: "extension_continued", Target: target, TargetLabel: "anchor_mean_reversion", Evidence: []string{"m5_extended_from_key_level", "m5_reversal_close"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"extension": q, "reversal": 1}}, FormedAt: episodeStart, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}

func extensionEpisodeStart(candles []market.Candle, anchor, threshold float64) int64 {
	index := len(candles) - 1
	for index > 0 && math.Abs(candles[index-1].Close-anchor) >= threshold {
		index--
	}
	return candles[index].Time
}
