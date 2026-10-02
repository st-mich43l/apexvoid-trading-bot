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
	boxBars, retestWindowBars                                         int
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
	retestWindow := 3
	if _, ok := c.Parameters["retest_window_bars"]; ok {
		retestWindow, e = strategyutil.Int(c.Parameters, "retest_window_bars")
		if e != nil {
			return nil, e
		}
	}
	s := &Strategy{boxBars: bars, retestWindowBars: retestWindow, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	vals := []*float64{&s.maximumWidthATR, &s.retestATR, &s.invalidationATR, &s.targetR, &s.expiryHours}
	for i, k := range []string{"maximum_width_atr", "retest_tolerance_atr", "invalidation_buffer_atr", "target_r", "expiry_hours"} {
		v, e := strategyutil.Float(c.Parameters, k)
		if e != nil {
			return nil, e
		}
		*vals[i] = v
	}
	if bars < 5 || retestWindow < 1 || s.maximumWidthATR <= 0 || s.retestATR <= 0 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 {
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
	retest := tf.Candles[n-1]
	var box []market.Candle
	direction := market.Direction("")
	level, low, high := 0.0, 0.0, 0.0
	for delay := 1; delay <= s.retestWindowBars; delay++ {
		breakoutIndex := n - 1 - delay
		boxStart := breakoutIndex - s.boxBars
		if boxStart < 0 {
			break
		}
		candidateBox := tf.Candles[boxStart:breakoutIndex]
		candidateLow, candidateHigh := candidateBox[0].Low, candidateBox[0].High
		for _, b := range candidateBox[1:] {
			candidateLow = math.Min(candidateLow, b.Low)
			candidateHigh = math.Max(candidateHigh, b.High)
		}
		if candidateHigh-candidateLow > s.maximumWidthATR*atr {
			continue
		}
		breakout := tf.Candles[breakoutIndex]
		between := tf.Candles[breakoutIndex+1 : n-1]
		upHeld, downHeld := true, true
		for _, b := range between {
			upHeld = upHeld && b.Close > candidateHigh
			downHeld = downHeld && b.Close < candidateLow
		}
		if breakout.Close > candidateHigh && upHeld && retest.Low >= candidateHigh-s.retestATR*atr && retest.Low <= candidateHigh+s.retestATR*atr && retest.Close > candidateHigh {
			direction, level = market.Buy, candidateHigh
		} else if breakout.Close < candidateLow && downHeld && retest.High >= candidateLow-s.retestATR*atr && retest.High <= candidateLow+s.retestATR*atr && retest.Close < candidateLow {
			direction, level = market.Sell, candidateLow
		}
		if direction.IsValid() {
			box, low, high = candidateBox, candidateLow, candidateHigh
			break
		}
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
