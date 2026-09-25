// Package crt implements the approved H1 candle-range sweep and M5 reclaim
// thesis. The H1 impulse candle owns the range; M5 confirms the trade.
package crt

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "crt"
const Version = "v2"

type Strategy struct {
	impulseATR, invalidationATR, expiryHours float64
	fingerprint                              string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("crt: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	for i, k := range []string{"minimum_h1_range_atr", "invalidation_buffer_atr", "expiry_hours"} {
		v, e := strategyutil.Float(c.Parameters, k)
		if e != nil {
			return nil, e
		}
		*[]*float64{&s.impulseATR, &s.invalidationATR, &s.expiryHours}[i] = v
	}
	if s.impulseATR <= 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("crt: invalid parameters")
	}
	return s, nil
}
func (s *Strategy) ID() strategy.StrategyID { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{market.H1, market.M5}
}
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	h := ctx.Timeframes[market.H1]
	m := ctx.Timeframes[market.M5]
	atr := ctx.Volatility.ATR
	if h == nil || m == nil || atr <= 0 || len(h.Candles) < 2 || len(m.Candles) < 2 {
		return nil
	}
	anchor := h.Candles[len(h.Candles)-2]
	if anchor.Range() < s.impulseATR*atr {
		return nil
	}
	bar := m.Candles[len(m.Candles)-1]
	direction := market.Direction("")
	if bar.Low < anchor.Low && bar.Close > anchor.Low {
		direction = market.Buy
	} else if bar.High > anchor.High && bar.Close < anchor.High {
		direction = market.Sell
	}
	if !direction.IsValid() {
		return nil
	}
	anchorEdge := anchor.Low
	entry := bar.Close
	invalid := math.Min(bar.Low, anchor.Low) - s.invalidationATR*atr
	target := anchor.High
	if direction == market.Sell {
		anchorEdge = anchor.High
		invalid = math.Max(bar.High, anchor.High) + s.invalidationATR*atr
		target = anchor.Low
	}
	q := strategyutil.Clamp01(anchor.Range()/(s.impulseATR*atr) - 0.25)
	c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("crt:%d", anchor.Time), Symbol: ctx.Symbol, Direction: direction, EntryLow: math.Min(entry, float64(anchorEdge)), EntryHigh: math.Max(entry, float64(anchorEdge)), Invalidation: invalid, InvalidationLabel: "crt_reclaim_failed", Target: target, TargetLabel: "opposite_h1_range", Evidence: []string{"h1_impulse_range", "m5_range_sweep_reclaim"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"h1_impulse": q, "m5_reclaim": 1}}, FormedAt: anchor.Time, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}
