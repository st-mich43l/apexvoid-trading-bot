// Package scalpbreakoutretest implements the distinct M5-context/M1-trigger
// scalp archetype: established box, M1 accepted break, then M1 retest.
package scalpbreakoutretest

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "scalp_breakout_retest"
const Version = "v2"

type Strategy struct {
	boxBars, acceptBars                                               int
	maximumWidthATR, retestATR, invalidationATR, targetR, expiryHours float64
	fingerprint                                                       string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("scalpbreakoutretest: wrong ID")
	}
	box, e := strategyutil.Int(c.Parameters, "m5_box_bars")
	if e != nil {
		return nil, e
	}
	accept, e := strategyutil.Int(c.Parameters, "m1_accept_bars")
	if e != nil {
		return nil, e
	}
	s := &Strategy{boxBars: box, acceptBars: accept, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	vals := []*float64{&s.maximumWidthATR, &s.retestATR, &s.invalidationATR, &s.targetR, &s.expiryHours}
	for i, k := range []string{"maximum_width_atr", "retest_tolerance_atr", "invalidation_buffer_atr", "target_r", "expiry_hours"} {
		v, e := strategyutil.Float(c.Parameters, k)
		if e != nil {
			return nil, e
		}
		*vals[i] = v
	}
	if box < 5 || accept < 1 || s.maximumWidthATR <= 0 || s.retestATR <= 0 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("scalpbreakoutretest: invalid parameters")
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
	if m5 == nil || m1 == nil || atr <= 0 || len(m5.Candles) < s.boxBars || len(m1.Candles) < s.acceptBars+1 {
		return nil
	}
	box := m5.Candles[len(m5.Candles)-s.boxBars:]
	low, high := box[0].Low, box[0].High
	for _, b := range box[1:] {
		low = math.Min(low, b.Low)
		high = math.Max(high, b.High)
	}
	if high-low > s.maximumWidthATR*atr {
		return nil
	}
	start := len(m1.Candles) - s.acceptBars - 1
	accepted := m1.Candles[start : start+s.acceptBars]
	retest := m1.Candles[len(m1.Candles)-1]
	direction := market.Direction("")
	level := 0.0
	up, down := true, true
	for _, b := range accepted {
		up = up && b.Close > high
		down = down && b.Close < low
	}
	if up && retest.Low >= high-s.retestATR*atr && retest.Low <= high+s.retestATR*atr && retest.Close > high {
		direction, level = market.Buy, high
	} else if down && retest.High >= low-s.retestATR*atr && retest.High <= low+s.retestATR*atr && retest.Close < low {
		direction, level = market.Sell, low
	}
	if !direction.IsValid() {
		return nil
	}
	invalid := level - s.invalidationATR*atr
	if direction == market.Sell {
		invalid = level + s.invalidationATR*atr
	}
	risk := math.Abs(retest.Close - invalid)
	target := retest.Close + s.targetR*risk
	if direction == market.Sell {
		target = retest.Close - s.targetR*risk
	}
	q := strategyutil.Clamp01(1 - (high-low)/(s.maximumWidthATR*atr))
	c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("scalp-box:%d:%.6f:%.6f", box[0].Time, low, high), Symbol: ctx.Symbol, Direction: direction, EntryLow: level - s.retestATR*atr, EntryHigh: level + s.retestATR*atr, Invalidation: invalid, InvalidationLabel: "m1_retest_failed", Target: target, TargetLabel: "m1_breakout_objective", Evidence: []string{"m5_prebreakout_box", "m1_breakout_accepted", "m1_retest_confirmed"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"m5_structure": q, "m1_acceptance": 1, "m1_retest": 1}}, FormedAt: box[0].Time, ConfirmedAt: retest.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}
