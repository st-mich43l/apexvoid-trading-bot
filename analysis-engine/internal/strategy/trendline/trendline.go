// Package trendline publishes the frozen trendline_reaction (V2) decision: a
// causally anchored line, in good health, that price touched from the correct
// side and closed back off, and that shows a confirmed structural reaction within
// the shared confluence floor.
//
// Two confirmed anchors establish the line (v7); the line is knowable from the
// close that confirmed its second anchor, and the trade is the first reaction after
// that, never the anchor itself. A validation touch after the anchors is not a
// requirement, only a measure of how respected the line is. The anchors are
// immutable and the line's health (tentative, broken, degraded, exhausted, stale) is
// judged on the frame's bounded window. A broken line, a test without a reaction,
// or an entry outside the interaction band is never published.
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
const Version = "v7"

type Strategy struct {
	strategyutil.RefusalCounter
	minimumValidationTouches, chopMinimumValidationTouches, maximumBarsSinceLastTouch int
	invalidationATR, expiryHours, minimumTargetDistanceATR                            float64
	interactionBandATR, closeViolationATR, approachMinDistanceATR                     float64
	chopRequireHTFAligned, requireHTFAligned, rejectExhausted                         bool
	legacy                                                                            strategyutil.LegacyDetectorSettings
	stopBudget                                                                        strategyutil.StopBudget
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
		"approach_min_distance_atr": &s.approachMinDistanceATR, "minimum_target_distance_atr": &s.minimumTargetDistanceATR,
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
	if s.stopBudget, err = strategyutil.ParseStopBudget(cfg.Parameters); err != nil {
		return nil, err
	}
	s.stopBudget.Refusals = &s.RefusalCounter
	if s.minimumValidationTouches < 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 || s.interactionBandATR <= 0 ||
		s.closeViolationATR <= 0 || s.approachMinDistanceATR < 0 || s.maximumBarsSinceLastTouch < 1 || s.minimumTargetDistanceATR <= 0 {
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
		// The line now: its price and interaction band at the latest closed bar, which is where the entry rests.
		interaction := technical.EvaluateInteraction(bars, line, base.ATR, cfg)
		if !holdsAtLatestBar(interaction.State) {
			continue
		}
		// The line is knowable from the close that confirmed its second anchor; the bars that formed and confirmed the anchor are the line's formation,
		// not a reaction to it. The reaction is a touch and, on or after it, a confirming candle, both inside the last reaction_lookback_bars closed bars;
		// they may be different candles (a wick that touched the line, a candle that closed back off it).
		conf := d.ReactionFrom(float64(interaction.BandLow), float64(interaction.BandHigh), nil, line.AnchorBConfirmedIndex+1)
		if conf == nil {
			continue
		}
		// The touch is judged where it happened: price reached the PROJECTED line at that bar, from the intended side, without closing through it.
		touch := technical.EvaluateInteraction(bars[:conf.TouchIndex+1], line, base.ATR, cfg)
		if touch.State != technical.InteractionReclaimedSupport && touch.State != technical.InteractionReclaimedResistance &&
			touch.State != technical.InteractionTestingSupport && touch.State != technical.InteractionTestingResistance {
			continue
		}
		linePrice := float64(interaction.LinePrice)
		zone := techniquezone.Zone{Bottom: linePrice - band, Top: linePrice + band, Side: side, Source: "trendline", BreakIndex: -1}
		if !d.EntryValid(zone) {
			continue
		}
		touches := 2 + len(line.ValidationTouches)
		factors := strategyutil.FactorsForConfirmation(strategyutil.ReactionFactors(conf.Type, d.HTFAligned(), touches, d.SessionSweepContext(conf)), conf.Type)
		low, high := float64(interaction.BandLow), float64(interaction.BandHigh)
		result := d.Finish(linePrice, zone, factors, line.Kind.String(), &low, &high)
		if result == nil {
			continue
		}
		if best == nil || result.Stars > best.Result.Stars {
			best = &strategyutil.TechniqueDecision{
				Technique: "trendline", Direction: direction, Detector: d, Confirmation: conf, Result: result,
				ID: lineID(line, bars),
				Score: func(dec *strategyutil.TechniqueDecision, g strategyutil.QualityGeometry) opportunity.StrategyQuality {
					return scoreQuality(line, touch, dec, g)
				},
			}
		}
	}
	candidate, ok := strategyutil.TechniqueCandidate(ctx, best, strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, AnchorToReaction: true, StopBudget: s.stopBudget, ZoneEvidence: "m5_trendline_causal_anchors", ConfirmedEvidence: "m5_trendline_reaction_confirmed",
		InvalidationLabel: "trendline_close_violation", InvalidationBufferATR: s.invalidationATR, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		// The target is the nearest opposing liquidity at least this far from the entry; with none there is
		// no technical objective and nothing is published (v3 invented twice the distance to the band edge).
		MinimumTargetDistanceATR: s.minimumTargetDistanceATR, RequireStructuralTarget: true,
	})
	if !ok {
		return nil
	}
	return []opportunity.Candidate{candidate}
}

// lineID is the line's identity: its kind and the open times of its two anchor candles. The swing ids the builder carries (legacy:<index>:<n>) are positions in
// the analysis window, which moves one bar every evaluation, so an id built from them named the same line differently on every bar and published the same
// reaction as a new opportunity each time.
func lineID(line technical.Trendline, bars []market.Candle) string {
	return fmt.Sprintf("trendline:%s:%d:%d", line.Kind, bars[line.AnchorAIndex].Time, bars[line.AnchorBIndex].Time)
}

// holdsAtLatestBar is whether the latest closed bar leaves the line standing: it is on the valid side of the line (reclaimed it, or has not come back to it).
// A close through the line, or a touch from the wrong side, is not a reaction to publish whatever happened on the bars before.
func holdsAtLatestBar(state technical.InteractionState) bool {
	switch state {
	case technical.InteractionReclaimedSupport, technical.InteractionReclaimedResistance,
		technical.InteractionAboveLine, technical.InteractionBelowLine,
		technical.InteractionApproachingSupport, technical.InteractionApproachingResistance:
		return true
	}
	return false
}

// structurallyRejected mirrors _trendline_v2_structural_rejection. A line with no validation is not rejected for that (v7): Tentative here means a line
// shorter than the minimum span, not one without a third touch. Staleness (the last validation too long ago) only exists for a line that has one.
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
