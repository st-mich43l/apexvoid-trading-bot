// Package liquiditysweep implements pool sweep, reclaim and opposite-close
// displacement. It is distinct from extension-based Snap Back.
package liquiditysweep

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "liquidity_sweep"
const Version = "v2"

type Strategy struct {
	minimumBodyATR, invalidationATR, targetR, expiryHours float64
	fingerprint                                           string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("liquiditysweep: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	vals := []*float64{&s.minimumBodyATR, &s.invalidationATR, &s.targetR, &s.expiryHours}
	keys := []string{"minimum_rejection_body_atr", "invalidation_buffer_atr", "target_r", "expiry_hours"}
	for i, k := range keys {
		v, e := strategyutil.Float(cfg.Parameters, k)
		if e != nil {
			return nil, e
		}
		*vals[i] = v
	}
	if s.minimumBodyATR <= 0 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("liquiditysweep: invalid parameters")
	}
	return s, nil
}
func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	bar, ok := strategyutil.LastBar(ctx, market.M5)
	atr := ctx.Volatility.ATR
	if tf == nil || !ok || atr <= 0 || bar.Body() < s.minimumBodyATR*atr {
		return nil
	}
	var out []opportunity.Candidate
	for _, p := range tf.Liquidity.Pools {
		if p.SweptAt == nil || p.ReclaimedAt == nil || *p.ReclaimedAt != bar.Time {
			continue
		}
		direction := market.Buy
		edge := float64(p.Low)
		if p.Side == liquidity.LiquidityBuySide {
			direction = market.Sell
			edge = float64(p.High)
		}
		if direction == market.Buy && !bar.IsBullish() {
			continue
		}
		if direction == market.Sell && !bar.IsBearish() {
			continue
		}
		invalid := edge - s.invalidationATR*atr
		if direction == market.Sell {
			invalid = edge + s.invalidationATR*atr
		}
		entry := bar.Close
		risk := math.Abs(entry - invalid)
		target := entry + s.targetR*risk
		if direction == market.Sell {
			target = entry - s.targetR*risk
		}
		q := strategyutil.Clamp01(bar.Body() / atr)
		c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: "sweep:" + p.ID, Symbol: ctx.Symbol, Direction: direction, EntryLow: math.Min(bar.Open, bar.Close), EntryHigh: math.Max(bar.Open, bar.Close), Invalidation: invalid, InvalidationLabel: "sweep_extreme_failed", Target: target, TargetLabel: "sweep_reversion_objective", Evidence: []string{"m5_liquidity_pool_swept", "m5_sweep_reclaimed", "m5_opposite_displacement"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"rejection_displacement": q, "reclaim": 1}}, FormedAt: *p.SweptAt, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
		if e == nil {
			out = append(out, c)
		}
	}
	return out
}
