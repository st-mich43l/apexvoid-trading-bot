// Package momentumride implements persistent directional displacement with
// a shallow continuation close and room to an unswept liquidity objective.
package momentumride

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "momentum_ride"
const Version = "v2"

type Strategy struct {
	minimumBodyATR, maxOverlap, invalidationATR, targetATR, expiryHours float64
	fingerprint                                                         string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("momentumride: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	vals := []*float64{&s.minimumBodyATR, &s.maxOverlap, &s.invalidationATR, &s.targetATR, &s.expiryHours}
	for i, k := range []string{"minimum_body_atr", "maximum_overlap_fraction", "invalidation_buffer_atr", "minimum_target_distance_atr", "expiry_hours"} {
		v, e := strategyutil.Float(c.Parameters, k)
		if e != nil {
			return nil, e
		}
		*vals[i] = v
	}
	if s.minimumBodyATR <= 0 || s.maxOverlap < 0 || s.maxOverlap >= 1 || s.invalidationATR <= 0 || s.targetATR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("momentumride: invalid parameters")
	}
	return s, nil
}
func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	atr := ctx.Volatility.ATR
	if tf == nil || atr <= 0 || len(tf.Candles) < 3 {
		return nil
	}
	a, b, c := tf.Candles[len(tf.Candles)-3], tf.Candles[len(tf.Candles)-2], tf.Candles[len(tf.Candles)-1]
	direction := market.Direction("")
	if a.IsBullish() && b.IsBullish() && c.IsBullish() {
		direction = market.Buy
	} else if a.IsBearish() && b.IsBearish() && c.IsBearish() {
		direction = market.Sell
	}
	if !direction.IsValid() || (a.Body()+b.Body()+c.Body())/3 < s.minimumBodyATR*atr {
		return nil
	}
	overlapAB := math.Max(0, math.Min(a.High, b.High)-math.Max(a.Low, b.Low)) / math.Max(math.Min(a.Range(), b.Range()), atr)
	overlapBC := math.Max(0, math.Min(b.High, c.High)-math.Max(b.Low, c.Low)) / math.Max(math.Min(b.Range(), c.Range()), atr)
	if math.Max(overlapAB, overlapBC) > s.maxOverlap {
		return nil
	}
	invalid := math.Min(math.Min(a.Low, b.Low), c.Low) - s.invalidationATR*atr
	from := c.Close
	if direction == market.Sell {
		invalid = math.Max(math.Max(a.High, b.High), c.High) + s.invalidationATR*atr
	}
	target, ok := strategyutil.OpposingLiquidity(tf.Liquidity.Pools, direction, from, s.targetATR*atr)
	if !ok {
		return nil
	}
	q := strategyutil.Clamp01((a.Body() + b.Body() + c.Body()) / (3 * atr))
	cand, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("momentum:%d", a.Time), Symbol: ctx.Symbol, Direction: direction, EntryLow: math.Min(c.Open, c.Close), EntryHigh: math.Max(c.Open, c.Close), Invalidation: invalid, InvalidationLabel: "momentum_structure_failed", Target: float64(target), TargetLabel: "momentum_liquidity_objective", Evidence: []string{"m5_persistent_direction", "m5_low_overlap_displacement"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"displacement": q, "continuity": 1}}, FormedAt: a.Time, ConfirmedAt: c.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{cand}
}
