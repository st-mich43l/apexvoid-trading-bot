// Package trendline publishes the frozen trendline_reaction (V2) decision: a
// causally anchored line with forward validation, in good health, that the latest
// closed bars reclaimed from the correct side, and that shows a confirmed
// structural reaction within the shared confluence floor.
//
// The line's A/B anchors are immutable and its health (tentative, broken,
// degraded, exhausted, stale) is judged on the frame's bounded window. A broken
// line, a test without a reaction, or an entry outside the interaction band is
// never published.
package trendline

import (
	"fmt"
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	technical "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

const ID strategy.StrategyID = "trendline"
const Version = "v3"

type Strategy struct {
	minimumValidationTouches, chopMinimumValidationTouches, maximumBarsSinceLastTouch int
	invalidationATR, expiryHours                                                      float64
	interactionBandATR, closeViolationATR, approachMinDistanceATR                     float64
	chopRequireHTFAligned, requireHTFAligned, rejectExhausted                         bool
	legacy                                                                            strategyutil.LegacyDetectorSettings
	fingerprint                                                                       string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("trendline: wrong strategy ID %q", cfg.ID)
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	var err error
	for key, dst := range map[string]*float64{
		"invalidation_buffer_atr": &s.invalidationATR, "expiry_hours": &s.expiryHours,
		"interaction_band_atr": &s.interactionBandATR, "close_violation_atr": &s.closeViolationATR,
		"approach_min_distance_atr": &s.approachMinDistanceATR,
	} {
		if *dst, err = strategyutil.Float(cfg.Parameters, key); err != nil {
			return nil, err
		}
	}
	for key, dst := range map[string]*int{
		"minimum_validation_touches": &s.minimumValidationTouches, "chop_minimum_validation_touches": &s.chopMinimumValidationTouches,
		"maximum_bars_since_last_touch": &s.maximumBarsSinceLastTouch,
	} {
		if *dst, err = strategyutil.Int(cfg.Parameters, key); err != nil {
			return nil, err
		}
	}
	for key, dst := range map[string]*bool{
		"chop_require_htf_aligned": &s.chopRequireHTFAligned, "require_htf_aligned": &s.requireHTFAligned, "reject_exhausted": &s.rejectExhausted,
	} {
		value, ok := cfg.Parameters[key].(bool)
		if !ok {
			return nil, fmt.Errorf("trendline: parameter %q must be boolean", key)
		}
		*dst = value
	}
	if s.legacy, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.minimumValidationTouches < 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 || s.interactionBandATR <= 0 ||
		s.closeViolationATR <= 0 || s.approachMinDistanceATR < 0 || s.maximumBarsSinceLastTouch < 1 {
		return nil, fmt.Errorf("trendline: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Evaluate mirrors _trendline_reaction_v2: lines nearest to price first, each
// gated on structure, then on the live interaction, then on a confirmed
// reaction; the best-scored qualifying line (the first on a tie) is published.
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	base, ok := strategyutil.NewLegacyDetectorForFrame(ctx, market.M5, s.legacy)
	if !ok {
		return nil
	}
	bars := base.Frame.Bars
	last := len(bars) - 1
	band := math.Max(1e-9, s.interactionBandATR*math.Max(0, base.ATR))
	lines := append([]technical.Trendline(nil), base.Frame.Trendlines...)
	sort.SliceStable(lines, func(i, j int) bool {
		return math.Abs(float64(technical.ValueAt(lines[i], last))-base.Price) < math.Abs(float64(technical.ValueAt(lines[j], last))-base.Price)
	})
	cfg := technical.Config{InteractionBandATR: s.interactionBandATR, CloseViolationATR: s.closeViolationATR, ApproachMinDistanceATR: s.approachMinDistanceATR}
	var best *strategyutil.TechniqueDecision
	for _, line := range lines {
		direction := market.Buy
		side := "demand"
		if line.Kind == technical.KindResistance {
			direction, side = market.Sell, "supply"
		} else if line.Kind != technical.KindSupport {
			continue
		}
		d := base.WithDirection(direction)
		if s.structurallyRejected(d, line, last) {
			continue
		}
		interaction := technical.EvaluateInteraction(bars, line, base.ATR, cfg)
		if interaction.State != technical.InteractionReclaimedSupport && interaction.State != technical.InteractionReclaimedResistance {
			continue
		}
		conf := d.Reaction(float64(interaction.BandLow), float64(interaction.BandHigh), nil)
		if conf == nil {
			continue
		}
		linePrice := float64(interaction.LinePrice)
		zone := techniquezone.Zone{Bottom: linePrice - band, Top: linePrice + band, Side: side, Source: "trendline", BreakIndex: -1}
		if !d.EntryValid(zone) {
			continue
		}
		touches := 2 + len(line.ValidationTouches)
		factors := strategyutil.FactorsForConfirmation(strategyutil.ReactionFactors(conf.Type, d.HTFAligned(), touches), conf.Type)
		low, high := float64(interaction.BandLow), float64(interaction.BandHigh)
		result := d.Finish(linePrice, zone, factors, line.Kind.String(), &low, &high)
		if result == nil {
			continue
		}
		if best == nil || result.Stars > best.Result.Stars {
			best = &strategyutil.TechniqueDecision{
				Technique: "trendline", Direction: direction, Detector: d, Confirmation: conf, Result: result,
				ID: fmt.Sprintf("trendline:%s:%s", line.AnchorA, line.AnchorB),
			}
		}
	}
	candidate, ok := strategyutil.TechniqueCandidate(ctx, best, strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, ZoneEvidence: "m5_trendline_causal_anchors", ConfirmedEvidence: "m5_trendline_reaction_confirmed",
		InvalidationLabel: "trendline_close_violation", InvalidationBufferATR: s.invalidationATR, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		Target: func(dec *strategyutil.TechniqueDecision) (float64, string) {
			// Two reward:risk beyond the entry, as the former strategy reported.
			entry := dec.Result.Level
			risk := math.Abs(entry - dec.Result.StructuralLow)
			if dec.Direction == market.Sell {
				risk = math.Abs(dec.Result.StructuralHigh - entry)
				return entry - 2*risk, "trendline_projection"
			}
			return entry + 2*risk, "trendline_projection"
		},
	})
	if !ok {
		return nil
	}
	return []opportunity.Candidate{candidate}
}

// structurallyRejected mirrors _trendline_v2_structural_rejection.
func (s *Strategy) structurallyRejected(d *strategyutil.LegacyDetector, line technical.Trendline, last int) bool {
	validations := len(line.ValidationTouches)
	if line.State == technical.StateTentative || validations < s.minimumValidationTouches {
		return true
	}
	if line.State == technical.StateBroken || line.BrokenAt != nil || line.State == technical.StateDegraded {
		return true
	}
	if line.State == technical.StateExhausted || s.rejectExhausted && line.Exhausted {
		return true
	}
	if validations > 0 && last-line.ValidationTouches[validations-1].BarIndex > s.maximumBarsSinceLastTouch {
		return true
	}
	aligned := d.HTFAligned()
	if s.requireHTFAligned && !aligned {
		return true
	}
	if d.Frame.Regime.Kind == "chop" {
		if validations < s.chopMinimumValidationTouches || s.chopRequireHTFAligned && !aligned {
			return true
		}
	}
	return false
}
