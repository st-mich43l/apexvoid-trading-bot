// Package snapback restores the frozen Python Snap Back thesis: a reversal
// back into a scored zone (or key level) after an extension, confirmed by a
// graded liquidity grab and a structural reaction. The decision runs on the
// engine's detector-contract frame so its direction, premium/discount gate,
// zone selection, sweep grade and confluence qualification are the frozen
// detector's.
package snapback

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "snap_back"
const Version = "v2"

type Strategy struct {
	extensionATR, invalidationATR, targetR, expiryHours float64
	extensionSource                                     string
	strictPD                                            bool
	detector                                            strategyutil.LegacyDetectorSettings
	fingerprint                                         string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("snapback: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*float64{
		"extension_atr": &s.extensionATR, "invalidation_buffer_atr": &s.invalidationATR,
		"target_r": &s.targetR, "expiry_hours": &s.expiryHours,
	} {
		v, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	var ok bool
	if s.extensionSource, ok = cfg.Parameters["extension_source"].(string); !ok || (s.extensionSource != "impulse" && s.extensionSource != "zone") {
		return nil, fmt.Errorf("snapback: extension_source must be impulse or zone")
	}
	if s.strictPD, ok = cfg.Parameters["strict_premium_discount"].(bool); !ok {
		return nil, fmt.Errorf("snapback: strict_premium_discount must be boolean")
	}
	var err error
	if s.detector, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.extensionATR <= 0 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("snapback: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	direction := strategyutil.StructuralDirection(ctx)
	if tf == nil || !direction.IsValid() || !strategyutil.PremiumDiscountAllows(tf, direction, s.strictPD) {
		return nil
	}
	d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, direction, s.detector)
	if !ok {
		return nil
	}

	zone, _, found := d.BestValidZone(d.CandidateZones())
	var (
		level, touches   = 0.0, 0
		structuralID     string
		structuralKind   = "demand"
		structuralSource = "supply_demand"
		structuralAgrees bool
	)
	if direction == market.Sell {
		structuralKind = "supply"
	}
	if found {
		level, touches, structuralAgrees, structuralID = d.ZoneKey(zone), zone.Touches, true, d.ZoneID(zone)
	} else {
		nearest, hasLevel := d.NearestLevel()
		if !hasLevel {
			return nil
		}
		zone = d.EntryZone(nearest.Price)
		level, touches, structuralID = nearest.Price, nearest.Touches, strategyutil.LevelID(nearest)
		structuralSource, structuralKind = "key_level", nearest.Kind
	}

	distance := s.extensionDistance(d, zone)
	if distance < d.ATR*s.extensionATR {
		return nil
	}
	grab := d.ZoneGrab(zone)
	if grab == nil || grab.Grade != "A" && grab.Grade != "B" {
		return nil
	}
	confirmation := d.Reaction(zone.Low(), zone.High(), grab)
	if confirmation == nil {
		return nil
	}
	factors := strategyutil.FactorsForConfirmation(confluence.Factors{
		HTFAligned: d.HTFAligned(), Touches: touches, WickRejection: true,
		DisplacementGrade: grab.Grade == "A", StructuralAgreement: structuralAgrees,
	}, confirmation.Type)
	structuralLow, structuralHigh := zone.Low(), zone.High()
	result := d.Finish(level, zone, factors, structuralKind, &structuralLow, &structuralHigh)
	if result == nil {
		return nil
	}

	low, high := result.Zone.Low(), result.Zone.High()
	invalid := low - s.invalidationATR*d.ATR
	entryReference := high
	if direction == market.Sell {
		invalid, entryReference = high+s.invalidationATR*d.ATR, low
	}
	risk := math.Abs(entryReference - invalid)
	target := entryReference + s.targetR*risk
	if direction == market.Sell {
		target = entryReference - s.targetR*risk
	}
	quality := strategyutil.Clamp01(.45 + .1*math.Min(float64(touches), 3) + .15*boolScore(grab.Grade == "A") + .1*strategyutil.Clamp01(distance/(s.extensionATR*d.ATR)-1))
	evidence := []string{"legacy_snap_extension_" + s.extensionSource, "legacy_pd_location", "liquidity_grab_grade_" + grab.Grade, "structural_reaction_" + confirmation.Type, "structural_source_" + structuralSource}
	formedAt := d.OriginTime(zone, confirmation.TouchTime)
	if formedAt > confirmation.ConfirmationTime {
		formedAt = confirmation.TouchTime
	}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: "snap:" + structuralID, Symbol: ctx.Symbol, Direction: direction,
		EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "snap_back_structure_failed",
		Target: target, TargetLabel: "snap_back_reversion", Evidence: evidence,
		Quality: opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{
			"extension": strategyutil.Clamp01(distance / (s.extensionATR * d.ATR)), "liquidity_grade": .75 + .25*boolScore(grab.Grade == "A"), "reaction": 1,
		}},
		FormedAt: formedAt, ConfirmedAt: confirmation.ConfirmationTime, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return nil
	}
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: structuralID, TouchBarTime: confirmation.TouchTime, ConfirmationBarTime: confirmation.ConfirmationTime,
		ReactionType: "rejection", Pattern: confirmation.Type,
	}
	candidate.DetectorConfluence = result.ConfluenceContext()
	return []opportunity.Candidate{candidate}
}

// extensionDistance mirrors _snap_back_extension_distance.
func (s *Strategy) extensionDistance(d *strategyutil.LegacyDetector, zone techniquezone.Zone) float64 {
	if s.extensionSource == "zone" {
		return d.ZoneDistance(zone)
	}
	kind := "high"
	if d.Direction == market.Buy {
		kind = "low"
	}
	last, found := 0.0, false
	for _, swing := range d.Frame.Swings {
		if swing.Kind == kind {
			last, found = swing.Price, true
		}
	}
	if !found {
		return d.ZoneDistance(zone)
	}
	if d.Direction == market.Buy {
		return math.Max(0, d.Price-last)
	}
	return math.Max(0, last-d.Price)
}

func boolScore(v bool) float64 {
	if v {
		return 1
	}
	return 0
}
