// Package impulsepullback implements M5 impulse qualification followed by a
// bounded M1 correction and continuation close.
package impulsepullback

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "impulse_pullback"
const Version = "v2"

type Strategy struct {
	minimumImpulseATR, minimumPullback, maximumPullback, invalidationATR, targetR, expiryHours float64
	fingerprint                                                                                string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("impulsepullback: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	vals := []*float64{&s.minimumImpulseATR, &s.minimumPullback, &s.maximumPullback, &s.invalidationATR, &s.targetR, &s.expiryHours}
	for i, k := range []string{"minimum_impulse_atr", "minimum_pullback_fraction", "maximum_pullback_fraction", "invalidation_buffer_atr", "target_r", "expiry_hours"} {
		v, e := strategyutil.Float(c.Parameters, k)
		if e != nil {
			return nil, e
		}
		*vals[i] = v
	}
	if s.minimumImpulseATR <= 0 || s.minimumPullback <= 0 || s.maximumPullback <= s.minimumPullback || s.maximumPullback >= 1 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("impulsepullback: invalid parameters")
	}
	return s, nil
}
func (s *Strategy) ID() strategy.StrategyID { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{market.M5, market.M1}
}
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	m5, m1 := ctx.Timeframes[market.M5], ctx.Timeframes[market.M1]
	atr := ctx.Volatility.ATR
	if m5 == nil || m1 == nil || atr <= 0 || len(m5.Candles) < 2 || len(m1.Candles) < 2 {
		return nil
	}
	imp := m5.Candles[len(m5.Candles)-2]
	if imp.Body() < s.minimumImpulseATR*atr {
		return nil
	}
	direction := market.Buy
	if imp.IsBearish() {
		direction = market.Sell
	} else if !imp.IsBullish() {
		return nil
	}
	pull, trigger := m1.Candles[len(m1.Candles)-2], m1.Candles[len(m1.Candles)-1]
	fraction := 0.0
	if direction == market.Buy {
		fraction = (imp.High - pull.Low) / imp.Range()
		if !trigger.IsBullish() || trigger.Close <= pull.High {
			return nil
		}
	} else {
		fraction = (pull.High - imp.Low) / imp.Range()
		if !trigger.IsBearish() || trigger.Close >= pull.Low {
			return nil
		}
	}
	if fraction < s.minimumPullback || fraction > s.maximumPullback {
		return nil
	}
	invalid := pull.Low - s.invalidationATR*atr
	if direction == market.Sell {
		invalid = pull.High + s.invalidationATR*atr
	}
	risk := math.Abs(trigger.Close - invalid)
	target := trigger.Close + s.targetR*risk
	if direction == market.Sell {
		target = trigger.Close - s.targetR*risk
	}
	q := strategyutil.Clamp01(1 - math.Abs(fraction-(s.minimumPullback+s.maximumPullback)/2))
	c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("impulse:%d", imp.Time), Symbol: ctx.Symbol, Direction: direction, EntryLow: math.Min(trigger.Open, trigger.Close), EntryHigh: math.Max(trigger.Open, trigger.Close), Invalidation: invalid, InvalidationLabel: "pullback_structure_failed", Target: target, TargetLabel: "impulse_continuation", Evidence: []string{"m5_qualified_impulse", "m1_bounded_pullback", "m1_continuation_trigger"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"pullback_quality": q, "continuation": 1}}, FormedAt: imp.Time, ConfirmedAt: trigger.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}
