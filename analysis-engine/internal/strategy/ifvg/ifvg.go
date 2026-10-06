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
	minimumStrength, minimumGapATR, invalidationATR, targetATR, expiryHours float64
	fingerprint                                                             string
	reaction                                                                strategyutil.ReactionConfig
	technique                                                               strategyutil.TechniqueGeometry
	legacy                                                                  strategyutil.LegacyDetectorSettings
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("ifvg: wrong strategy ID %q", cfg.ID)
	}
	values := []*float64{}
	keys := []string{"minimum_strength", "minimum_gap_atr", "invalidation_buffer_atr", "minimum_target_distance_atr", "expiry_hours"}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	values = append(values, &s.minimumStrength, &s.minimumGapATR, &s.invalidationATR, &s.targetATR, &s.expiryHours)
	for i, key := range keys {
		value, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*values[i] = value
	}
	reaction, err := strategyutil.ParseReactionConfig(cfg.Parameters)
	if err != nil {
		return nil, err
	}
	s.reaction = reaction
	technique, err := strategyutil.ParseTechniqueGeometry(cfg.Parameters)
	if err != nil {
		return nil, err
	}
	s.technique = technique
	if s.legacy, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.minimumStrength < 0 || s.minimumGapATR < 0 || s.invalidationATR <= 0 || s.targetATR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("ifvg: invalid non-positive strategy parameter")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Evaluate emits the resting inversion observations and, separately, the
// confirmed reaction the frozen iFVG publisher
// (technique_detectors.ifvg_technique_reaction) decides on the frame's
// technique instances.
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	return append(s.resting(ctx), strategyutil.ConfirmedTechnique(ctx, s.legacy, strategyutil.TechniqueIFVG, "", strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, ZoneEvidence: "m5_ifvg_inversion_confirmed", ConfirmedEvidence: "m5_ifvg_rejection_confirmed",
		InvalidationLabel: "ifvg_inversion_failed", InvalidationBufferATR: s.invalidationATR,
		MinimumTargetDistanceATR: s.targetATR, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
	})...)
}

func (s *Strategy) resting(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
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
		// The inverted gap keeps its original width; one that is a sliver of
		// ATR is noise (production 2026-09-30: a 0.10 ATR iFVG stopped for
		// -13 pips inside a minute).
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
