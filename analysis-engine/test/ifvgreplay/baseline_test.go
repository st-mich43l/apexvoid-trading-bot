package ifvgreplay_test

// This file is iFVG v2 exactly as it shipped (the frozen Python publisher's decision
// over the zone chain), kept here, outside production, only so the replay
// certification can run the old and the new logic over the SAME contexts and report
// the difference. Do not import it from production code.

import (
	"fmt"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

const frozenID strategy.StrategyID = "ifvg"
const frozenVersion = "v2"

type frozenV2 struct {
	minimumStrength, minimumGapATR, invalidationATR, targetATR, expiryHours float64
	fingerprint                                                             string
	legacy                                                                  strategyutil.LegacyDetectorSettings
}

func newFrozenV2(cfg strategy.Config) (*frozenV2, error) {
	s := &frozenV2{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*float64{
		"minimum_strength": &s.minimumStrength, "minimum_gap_atr": &s.minimumGapATR, "invalidation_buffer_atr": &s.invalidationATR,
		"minimum_target_distance_atr": &s.targetATR, "expiry_hours": &s.expiryHours,
	} {
		value, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = value
	}
	legacy, err := strategyutil.ParseLegacyDetectorSettings(cfg.Parameters)
	if err != nil {
		return nil, err
	}
	s.legacy = legacy
	if s.minimumStrength < 0 || s.minimumGapATR < 0 || s.invalidationATR <= 0 || s.targetATR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("ifvg: invalid non-positive strategy parameter")
	}
	return s, nil
}

// Evaluate is the v2 decision: the resting inversion observations and, separately,
// the confirmed reaction the frozen iFVG publisher decides on the frame's technique
// instances.
func (s *frozenV2) Evaluate(ctx *analysiscontext.MarketContext) (resting, confirmed []opportunity.Candidate) {
	confirmed = strategyutil.ConfirmedFrozenTechnique(ctx, s.legacy, strategyutil.TechniqueIFVG, "", strategyutil.TechniqueSpec{
		ID: string(frozenID), Version: frozenVersion, ZoneEvidence: "m5_ifvg_inversion_confirmed", ConfirmedEvidence: "m5_ifvg_rejection_confirmed",
		InvalidationLabel: "ifvg_inversion_failed", InvalidationBufferATR: s.invalidationATR,
		MinimumTargetDistanceATR: s.targetATR, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
	})
	return s.resting(ctx), confirmed
}

func (s *frozenV2) resting(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	bar, ok := strategyutil.LastBar(ctx, market.M5)
	if tf == nil || !ok || ctx.Volatility.ATR <= 0 {
		return nil
	}
	atr := ctx.Volatility.ATR
	var result []opportunity.Candidate
	seen := map[string]struct{}{}
	for _, z := range tf.Zones.Zones {
		if z.Kind != zone.KindIFVG || z.Strength < s.minimumStrength ||
			(z.State != zone.StateFresh && z.State != zone.StateTouched) ||
			(z.Relevance != zone.Immediate && z.Relevance != zone.Nearby) {
			continue
		}
		if float64(z.High-z.Low) < s.minimumGapATR*atr {
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
			ID: string(frozenID), Version: frozenVersion, SetupKey: "ifvg:" + z.ID, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: float64(z.Low), EntryHigh: float64(z.High), Invalidation: invalidation,
			InvalidationLabel: "ifvg_inversion_failed", Target: float64(target), TargetLabel: "opposing_liquidity",
			Evidence: []string{"m5_ifvg_inversion_confirmed", "m5_ifvg_reaction"},
			Quality:  opportunity.StrategyQuality{Overall: strategyutil.Clamp01(0.7*z.Strength + 0.3), Components: map[string]float64{"inversion_strength": z.Strength, "freshness": 1}},
			FormedAt: z.OriginTime, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		})
		if err == nil {
			if _, dup := seen[candidate.ID]; dup {
				continue
			}
			seen[candidate.ID] = struct{}{}
			result = append(result, candidate)
		}
	}
	return result
}
