// Package rangesweep implements an M5-established range whose edge is swept
// and reclaimed on M1. It is distinct from M5 Range Edge rejection history.
package rangesweep

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

const ID strategy.StrategyID = "range_sweep"
const Version = "v2"

type Strategy struct {
	rangeBars                              int
	sweepATR, invalidationATR, expiryHours float64
	fingerprint                            string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("rangesweep: wrong ID")
	}
	bars, e := strategyutil.Int(c.Parameters, "range_bars")
	if e != nil {
		return nil, e
	}
	sweep, e := strategyutil.Float(c.Parameters, "minimum_sweep_atr")
	if e != nil {
		return nil, e
	}
	invalid, e := strategyutil.Float(c.Parameters, "invalidation_buffer_atr")
	if e != nil {
		return nil, e
	}
	expiry, e := strategyutil.Float(c.Parameters, "expiry_hours")
	if e != nil {
		return nil, e
	}
	if bars < 5 || sweep <= 0 || invalid <= 0 || expiry <= 0 {
		return nil, fmt.Errorf("rangesweep: invalid parameters")
	}
	return &Strategy{bars, sweep, invalid, expiry, strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}, nil
}
func (s *Strategy) ID() strategy.StrategyID { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{market.M5, market.M1}
}
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	m5, m1 := ctx.Timeframes[market.M5], ctx.Timeframes[market.M1]
	atr := ctx.Volatility.ATR
	if m5 == nil || m1 == nil || atr <= 0 || len(m5.Candles) < s.rangeBars || len(m1.Candles) < 1 || m5.Structure.Internal.Trend != structure.TrendRange {
		return nil
	}
	box := m5.Candles[len(m5.Candles)-s.rangeBars:]
	low, high := box[0].Low, box[0].High
	for _, b := range box[1:] {
		low = math.Min(low, b.Low)
		high = math.Max(high, b.High)
	}
	bar := m1.Candles[len(m1.Candles)-1]
	direction := market.Direction("")
	edge := 0.0
	if low-bar.Low >= s.sweepATR*atr && bar.Close > low {
		direction, edge = market.Buy, low
	} else if bar.High-high >= s.sweepATR*atr && bar.Close < high {
		direction, edge = market.Sell, high
	}
	if !direction.IsValid() {
		return nil
	}
	invalid, target := bar.Low-s.invalidationATR*atr, high
	if direction == market.Sell {
		invalid, target = bar.High+s.invalidationATR*atr, low
	}
	q := strategyutil.Clamp01(math.Abs(bar.Close-edge) / atr)
	c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("range-sweep:%d:%d", box[0].Time, bar.Time), Symbol: ctx.Symbol, Direction: direction, EntryLow: math.Min(bar.Open, bar.Close), EntryHigh: math.Max(bar.Open, bar.Close), Invalidation: invalid, InvalidationLabel: "m1_sweep_extreme", Target: target, TargetLabel: "opposite_m5_range_edge", Evidence: []string{"m5_range_context", "m1_edge_sweep", "m1_reclaim"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"sweep_depth": q, "reclaim": 1}}, FormedAt: box[0].Time, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}
