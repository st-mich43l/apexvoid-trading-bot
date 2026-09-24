// Package ifvg implements the inverse-FVG thesis: a fair-value gap is
// decisively closed through, becomes a canonical iFVG, then reacts from the
// inverted side while the inversion remains structurally valid.
package ifvg

import (
	"fmt"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

const ID strategy.StrategyID = "ifvg"
const Version = "v2"

type Strategy struct {
	minimumStrength, invalidationATR, targetATR, expiryHours float64
	fingerprint                                              string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("ifvg: wrong strategy ID %q", cfg.ID)
	}
	values := []*float64{}
	keys := []string{"minimum_strength", "invalidation_buffer_atr", "minimum_target_distance_atr", "expiry_hours"}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	values = append(values, &s.minimumStrength, &s.invalidationATR, &s.targetATR, &s.expiryHours)
	for i, key := range keys {
		value, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*values[i] = value
	}
	if s.minimumStrength < 0 || s.invalidationATR <= 0 || s.targetATR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("ifvg: invalid non-positive strategy parameter")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	bar, ok := strategyutil.LastBar(ctx, market.M5)
	if tf == nil || !ok || ctx.Volatility.ATR <= 0 {
		return nil
	}
	atr := ctx.Volatility.ATR
	var result []opportunity.Candidate
	seenCandidates := make(map[string]struct{})
	for _, z := range tf.Zones.Zones {
		if z.Kind != zone.KindIFVG || z.Strength < s.minimumStrength ||
			(z.State != zone.StateFresh && z.State != zone.StateTouched) ||
			(z.Relevance != zone.Immediate && z.Relevance != zone.Nearby) {
			continue
		}
		direction := z.Side.Direction()
		from := float64(z.High)
		invalidation := float64(z.Low) - s.invalidationATR*atr
		if direction == market.Sell {
			from = float64(z.Low)
			invalidation = float64(z.High) + s.invalidationATR*atr
		}
		target, ok := strategyutil.OpposingLiquidity(tf.Liquidity.Pools, direction, from, s.targetATR*atr)
		if !ok {
			continue
		}
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: "ifvg:" + z.ID, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: float64(z.Low), EntryHigh: float64(z.High), Invalidation: invalidation,
			InvalidationLabel: "ifvg_inversion_failed", Target: float64(target), TargetLabel: "opposing_liquidity",
			Evidence: []string{"m5_ifvg_inversion_confirmed", "m5_ifvg_reaction"},
			Quality:  opportunity.StrategyQuality{Overall: strategyutil.Clamp01(0.7*z.Strength + 0.3), Components: map[string]float64{"inversion_strength": z.Strength, "freshness": 1}},
			FormedAt: z.OriginTime, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		})
		if err == nil {
			if _, duplicate := seenCandidates[candidate.ID]; duplicate {
				// Canonical zone state can temporarily contain repeated copies
				// of the same semantic iFVG while its source history converges.
				// Candidate identity is intentionally based on that canonical
				// zone ID, so emit it once rather than failing the whole closed
				// bar evaluation downstream.
				continue
			}
			seenCandidates[candidate.ID] = struct{}{}
			result = append(result, candidate)
		}
	}
	return result
}
