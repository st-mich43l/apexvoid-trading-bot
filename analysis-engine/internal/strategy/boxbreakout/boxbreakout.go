// Package boxbreakout implements M5 compression, accepted breakout and retest.
// It remains independently rollout-controlled from the M1 scalp archetype.
package boxbreakout

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "box_breakout"
const Version = "v2"

type Strategy struct {
	boxBars                                                           int
	maximumWidthATR, retestATR, invalidationATR, targetR, expiryHours float64
	fingerprint                                                       string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("boxbreakout: wrong ID")
	}
	bars, e := strategyutil.Int(c.Parameters, "box_bars")
	if e != nil {
		return nil, e
	}
	s := &Strategy{boxBars: bars, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	vals := []*float64{&s.maximumWidthATR, &s.retestATR, &s.invalidationATR, &s.targetR, &s.expiryHours}
	for i, k := range []string{"maximum_width_atr", "retest_tolerance_atr", "invalidation_buffer_atr", "target_r", "expiry_hours"} {
		v, e := strategyutil.Float(c.Parameters, k)
		if e != nil {
			return nil, e
		}
		*vals[i] = v
	}
	if bars < 5 || s.maximumWidthATR <= 0 || s.retestATR <= 0 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("boxbreakout: invalid parameters")
	}
	return s, nil
}
func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	atr := ctx.Volatility.ATR
	if tf == nil || atr <= 0 || len(tf.Candles) < s.boxBars+2 {
		return nil
	}
	n := len(tf.Candles)
	box := tf.Candles[n-s.boxBars-2 : n-2]
	low, high := box[0].Low, box[0].High
	for _, b := range box[1:] {
		low = math.Min(low, b.Low)
		high = math.Max(high, b.High)
	}
	if high-low > s.maximumWidthATR*atr {
		return nil
	}
	breakout, retest := tf.Candles[n-2], tf.Candles[n-1]
	direction := market.Direction("")
	level := 0.0
	if breakout.Close > high && retest.Low >= high-s.retestATR*atr && retest.Low <= high+s.retestATR*atr && retest.Close > high {
		direction, level = market.Buy, high
	} else if breakout.Close < low && retest.High >= low-s.retestATR*atr && retest.High <= low+s.retestATR*atr && retest.Close < low {
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
	c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("box:%d:%.6f:%.6f", box[0].Time, low, high), Symbol: ctx.Symbol, Direction: direction, EntryLow: level - s.retestATR*atr, EntryHigh: level + s.retestATR*atr, Invalidation: invalid, InvalidationLabel: "box_reentry", Target: target, TargetLabel: "box_measured_move", Evidence: []string{"m5_box_compression", "m5_breakout_accepted", "m5_box_retest"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"compression": q, "acceptance": 1, "retest": 1}}, FormedAt: box[0].Time, ConfirmedAt: retest.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}
