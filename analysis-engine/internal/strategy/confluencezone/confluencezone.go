// Package confluencezone is the catalog's compositional exception. It trades
// overlap among already-canonical zones; it never invokes another strategy.
package confluencezone

import (
	"fmt"
	"math"
	"sort"
	"strings"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

const ID strategy.StrategyID = "confluence_zone"
const Version = "v2"

type Strategy struct {
	minimumFacts                            int
	invalidationATR, targetATR, expiryHours float64
	fingerprint                             string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("confluencezone: wrong ID")
	}
	facts, e := strategyutil.Int(c.Parameters, "minimum_facts")
	if e != nil {
		return nil, e
	}
	invalid, e := strategyutil.Float(c.Parameters, "invalidation_buffer_atr")
	if e != nil {
		return nil, e
	}
	target, e := strategyutil.Float(c.Parameters, "minimum_target_distance_atr")
	if e != nil {
		return nil, e
	}
	expiry, e := strategyutil.Float(c.Parameters, "expiry_hours")
	if e != nil {
		return nil, e
	}
	if facts < 2 || invalid <= 0 || target <= 0 || expiry <= 0 {
		return nil, fmt.Errorf("confluencezone: invalid parameters")
	}
	return &Strategy{facts, invalid, target, expiry, strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}, nil
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
	zs := tf.Zones.Zones
	for i := 0; i < len(zs); i++ {
		a := zs[i]
		if a.State == zone.StateInvalidated || a.State == zone.StateMitigated {
			continue
		}
		low, high := float64(a.Low), float64(a.High)
		ids := []string{a.ID}
		facts := 1
		for j := i + 1; j < len(zs); j++ {
			b := zs[j]
			if b.Side != a.Side || b.Kind == a.Kind || b.State == zone.StateInvalidated || b.State == zone.StateMitigated {
				continue
			}
			lo, hi := math.Max(low, float64(b.Low)), math.Min(high, float64(b.High))
			if lo <= hi {
				low, high, ids, facts = lo, hi, append(ids, b.ID), facts+1
			}
		}
		if facts < s.minimumFacts || bar.Low > high || bar.High < low {
			continue
		}
		direction := a.Side.Direction()
		invalid := low - s.invalidationATR*atr
		from := high
		if direction == market.Sell {
			invalid = high + s.invalidationATR*atr
			from = low
		}
		target, found := strategyutil.OpposingLiquidity(tf.Liquidity.Pools, direction, from, s.targetATR*atr)
		if !found {
			continue
		}
		q := strategyutil.Clamp01(float64(facts) / 4)
		sort.Strings(ids)
		c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: "overlap:" + strings.Join(ids, "+"), Symbol: ctx.Symbol, Direction: direction, EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "confluence_overlap_failed", Target: float64(target), TargetLabel: "opposing_liquidity", Evidence: []string{"m5_distinct_zone_overlap", "m5_confluence_reaction"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"independent_facts": q, "location": 1}}, FormedAt: a.OriginTime, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
		if e == nil {
			return []opportunity.Candidate{c}
		}
	}
	return nil
}
