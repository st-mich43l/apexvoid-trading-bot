package demand

import (
	"fmt"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"math"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const fixedRewardRisk = "fixed_reward_risk"

const (
	zoneEvidence      = "m5_demand_zone_confirmed"
	confirmedEvidence = "m5_demand_zone_rejection_confirmed"
	invalidationLabel = "demand_zone_invalidated"
	// reactionExtremeEvidence marks a stop that the reaction's own traded extreme set (it lay beyond the whole zone).
	// discountEvidence marks a zone that earned the discount points of its zone score.
	discountEvidence        = "m5_demand_zone_discount"
	reactionExtremeEvidence = "invalidation_beyond_reaction_extreme"
)

// reactionAnchoredInvalidation puts the stop below the whole zone and below the extreme the bars from the touch to the confirmation traded, plus the buffer;
// anchored is true when the reaction's extreme (not the zone's edge) set it.
func reactionAnchoredInvalidation(bars []market.Candle, conf *confirmation, structLow, buffer float64) (price float64, anchored bool) {
	structural := structLow - buffer
	if conf == nil || conf.TouchIndex < 0 || conf.ConfirmationIndex >= len(bars) || conf.TouchIndex > conf.ConfirmationIndex {
		return structural, false
	}
	extreme := math.Inf(1)
	for _, b := range bars[conf.TouchIndex : conf.ConfirmationIndex+1] {
		extreme = math.Min(extreme, b.Low)
	}
	if math.IsInf(extreme, 0) || math.IsNaN(extreme) || !(extreme < structLow) {
		return structural, false
	}
	return extreme - buffer, true
}

// candidate builds the confirmed opportunity: the published (proximal) entry band, the stop beyond the whole zone and the reaction's extreme, the
// nearest opposing structure (or a fixed reward:risk when none stands in the way) as the target, and the decision's own reaction and confluence as evidence.
func (s *Strategy) candidate(ctx candidateContext, d *decision) (opportunity.Candidate, bool) {
	v := d.v
	atr := v.atr
	if !v.atrKnown {
		atr = math.NaN() // missing evidence: quality gives no credit for a measure that needs it, and never reads the admission placeholder as an ATR
	}
	low, high := d.entryLow, d.entryHigh
	structLow, structHigh := d.res.structuralLow, d.res.structuralHigh
	reference, invalidation := d.reference, d.invalidation
	target, targetLabel := d.room.Target, d.room.Label
	conf := d.conf
	formedAt, confirmedAt := conf.TouchTime, conf.ConfirmationTime
	setupKey := fmt.Sprintf("zone:%s:rejection:%d:%d", d.id, conf.TouchTime, conf.ConfirmationTime)
	evidence := []string{zoneEvidence}
	if conf.Type != "" { // the confirmation evidence is published only when a confirming candle showed it
		evidence = append(evidence, confirmedEvidence)
	} else {
		evidence = append(evidence, strategyutil.CandleConfirmationOptionalEvidence)
	}
	if d.inst.Timeframe != "" {
		evidence = append(evidence, "htf_zone_"+lowerTimeframe(d.inst.Timeframe))
	}
	if d.anchored {
		evidence = append(evidence, reactionExtremeEvidence)
	}
	if d.inst.Discount {
		evidence = append(evidence, discountEvidence)
	}
	if d.budgetCode != "" {
		evidence = append(evidence, d.budgetCode)
	}
	if f := d.inst.Formation; f != nil {
		evidence = append(evidence, "major_swing_"+strings.ToLower(f.Swing.Class.String()), "zone_strength_"+f.Strength.String())
	}
	if chosen, ok := d.plan.Chosen(); ok {
		evidence = append(evidence, "entry_"+chosen.Kind)
	}
	scored := s.quality(ctx, d, reference, invalidation, target, targetLabel != fixedRewardRisk, atr)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.symbol, Direction: market.Buy,
		EntryLow: low, EntryHigh: high, Invalidation: invalidation, InvalidationLabel: invalidationLabel,
		Target: target, TargetLabel: targetLabel, Evidence: evidence, Quality: scored,
		FormedAt: formedAt, ConfirmedAt: confirmedAt, ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	candidate.StructuralID = d.id
	candidate.ZoneID = d.id
	if d.inst.Timeframe != "" {
		candidate.StructureTimeframe = d.inst.Timeframe
	}
	candidate.Structure = s.structureZone(d, structLow, structHigh)
	candidate.Structure.Swing = zoneSwing(d)
	candidate.EntryPlan = entryPlan(d)
	if conf.Type != "" {
		candidate.Reaction = &opportunity.ReactionConfirmation{
			ZoneID: d.id, TouchBarTime: conf.TouchTime, ConfirmationBarTime: conf.ConfirmationTime, ReactionType: "rejection", Pattern: conf.Type,
		}
	}
	candidate.DetectorConfluence = d.res.confluenceContext()
	candidate.Provenance = opportunity.AnalysisProvenance{
		StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
		ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
	}
	return candidate, true
}

type candidateContext struct {
	symbol market.Symbol
	market *analysiscontext.MarketContext // the closed-bar context the quality room reads its opposing barriers from
}

// structureZone is the whole zone as drawn on its own timeframe (the execution frame is M5), with the open time of the bar that formed it.
func (s *Strategy) structureZone(d *decision, low, high float64) *opportunity.StructureZone {
	timeframe := market.M5
	if d.inst.Timeframe != "" {
		timeframe = d.inst.Timeframe
	}
	formedAt := d.inst.OriginTime
	return &opportunity.StructureZone{Timeframe: timeframe, Low: low, High: high, FormedAt: formedAt}
}

// quality is the technical quality of a confirmed reaction off a demand zone: seven dimensions, each in [0, 1], averaged without weights (informational).
func (s *Strategy) quality(ctx candidateContext, d *decision, reference, invalidation, target float64, structural bool, atr float64) opportunity.StrategyQuality {
	if d.inst.Formation != nil {
		return s.qualityV4(ctx, d, reference, invalidation, target, structural, atr)
	}
	return s.qualityV2(ctx, d, reference, invalidation, target, structural, atr)
}

// qualityV2 is the legacy zone's model (zone_model: legacy), frozen exactly as it was: the rollback restores the earlier scoring too. Its width is read against the execution ATR, which
// is the defect supply|demand.quality.v4 corrects for swing zones.
func (s *Strategy) qualityV2(ctx candidateContext, d *decision, reference, invalidation, target float64, structural bool, atr float64) opportunity.StrategyQuality {
	m := quality.New(string(ID) + ".quality.v2")
	v := d.v
	departure, width, touches := math.NaN(), math.NaN(), math.NaN()
	if atr > 0 {
		lo, hi := d.inst.Low, d.inst.High
		if d.inst.StructuralHigh > d.inst.StructuralLow && d.inst.StructuralLow > 0 {
			lo, hi = d.inst.StructuralLow, d.inst.StructuralHigh
		}
		width = (hi - lo) / atr
		touches = float64(d.inst.Touches)
		bars := v.frame.Bars
		end := len(bars)
		if d.conf.TouchIndex < end {
			end = d.conf.TouchIndex
		}
		// The departure is read on the M5 bars right after the origin: unknown once the origin has left the M5 window.
		start := d.inst.OriginIndex
		if stop := minInt(end, start+9); start >= 0 && start < stop {
			best := 0.0
			for _, b := range bars[start:stop] {
				best = math.Max(best, b.High-hi)
			}
			departure = best / atr
		}
	}
	m.Score("zone_departure", quality.Ramp(departure, 1, 3))
	m.Score("zone_precision", quality.Peak(width, 0.1, 0.4, 1.5, 3.5))
	m.Score("zone_freshness", quality.Fall(touches, 1, 5))
	m.Measure("zone_departure_atr", departure)
	m.Measure("zone_width_atr", width)
	m.Measure("zone_touches", touches)
	m.Score("rejection", quality.RejectionScore(v.frame.Bars, d.conf.TouchIndex, d.conf.ConfirmationIndex, true))
	strategyutil.ScoreEntryLocation(m, true, v.price, reference, atr)
	m.Score("structure", quality.StructureScore(true, v.frame.Structure, v.recentCHoCH(maxInt(1, v.cfg.ReactionLookbackBars)), v.htfAligned()))
	pip := d.v.cfg.StopBudget.PipSize
	if !(pip > 0) {
		pip = d.v.cfg.PipSize
	}
	strategyutil.ScoreRoom(m, strategyutil.MeasureQualityRoom(ctx.market, true, reference, invalidation, target, structural, d.v.cfg.StopBudget, d.v.cfg.Barrier.RoomParams(pip, strategyutil.ScalpRoomATR(ctx.market, atr)), 0), pip)
	m.Measure("room_r", d.room.RoomR)
	m.Measure("pd_position", d.inst.PDPosition)
	m.Measure("deep_entry_pips", deepEntryPips(d))
	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}

// qualityV4 is the technical quality of a confirmed reaction off a swing-anchored demand zone: ten dimensions, each in [0, 1], averaged without weights (informational, never a gate and
// never a probability of winning). Every dimension is measured from information available at its own time, in the units of its own timeframe:
//
// FORMATION (the structure frame's own ATR; evidence up to the formation bar, fixed from then on):
//
//	zone_departure          Ramp(net move of the departure that left the base / ATR at its last bar; 1, 3)
//	zone_precision          Peak(structural width / ATR at the departure bar; 0.1, 0.4, 1.5, 3.5): the whole validated base, never the entry slice; the zone validity bounds are [0.2, 3.0]
//	swing_significance      Ramp(approach: how far price travelled INTO the swing within the external horizon / ATR; intermediate prominence, twice the major one): the departure is not in it
//	displacement_efficiency Ramp(net move / total travel of the departure bars; 0.4, 0.8): a different property from its size
//	structure_shift         1 when the departure confirmed a BOS or a CHoCH by the formation bar, else 0 (neither kind is worth more as a supply/demand origin; the kind is published)
//
// DECISION TIME (the execution frame, the bar being decided):
//
//	zone_freshness          Fall(touches the zone has taken; 0, the touches the strategy allows + 1)
//	rejection               the candles of a RECOGNISED reaction (a touch with no recognised reaction earns 0: it stays eligible, it just has no reaction evidence)
//	entry_location          the chase of the M5 entry reference in M5 ATR (no credit without a valid M5 ATR)
//	structure               the M5 market structure for the trade and the higher timeframe
//	room                    the structural room over the effective risk, limited by the canonical opposing barrier
//
// Nothing is fabricated: a missing ATR gives no credit to the dimension that needs it and the evidence is published (structure_atr_available, execution_atr_available).
func (s *Strategy) qualityV4(ctx candidateContext, d *decision, reference, invalidation, target float64, structural bool, atr float64) opportunity.StrategyQuality {
	m := quality.New(string(ID) + ".quality.v4")
	v := d.v
	f := d.inst.Formation
	major := v.cfg.Formation.Zone.Major

	m.Score("zone_departure", quality.Ramp(f.DepartureATR, 1, 3))
	m.Score("zone_precision", quality.Peak(f.WidthATR, 0.1, 0.4, 1.5, 3.5))
	m.Score("swing_significance", quality.Ramp(f.Swing.ApproachATR, major.IntermediateProminenceATR, 2*major.MajorProminenceATR))
	m.Score("displacement_efficiency", quality.Ramp(f.Efficiency, 0.4, 0.8))
	shift := 0.0
	if f.Swing.Shift != "" {
		shift = 1
	}
	m.Score("structure_shift", shift)
	m.Measure("zone_departure_atr", f.DepartureATR)
	m.Measure("zone_width_atr", f.WidthATR)
	m.Measure("swing_approach_atr", f.Swing.ApproachATR)
	m.Measure("swing_prominence_atr", f.Swing.ProminenceATR)
	m.Measure("structure_shift_kind", map[string]float64{"CHoCH": 1, "BOS": 2}[f.Swing.Shift])
	m.Measure("swing_class", float64(f.Swing.Class))
	m.Measure("zone_strength", float64(f.Strength))
	m.Measure("structure_atr_available", boolFloat(f.WidthATR > 0 && !math.IsInf(f.WidthATR, 0)))

	allowed := float64(s.cfg.MaximumZoneTouches + 1)
	if s.cfg.MaximumZoneTouches <= 0 {
		allowed = 5
	}
	m.Score("zone_freshness", quality.Fall(float64(d.inst.Touches), 0, allowed))
	m.Measure("zone_touches", float64(d.inst.Touches))
	reacted := d.conf != nil && d.conf.Type != ""
	if reacted {
		m.Score("rejection", quality.RejectionScore(v.frame.Bars, d.conf.TouchIndex, d.conf.ConfirmationIndex, true))
	} else {
		m.Score("rejection", 0)
	}
	m.Measure("reaction_confirmed", boolFloat(reacted))
	strategyutil.ScoreEntryLocation(m, true, v.price, reference, atr)
	m.Measure("execution_atr_available", boolFloat(atr > 0 && !math.IsInf(atr, 0)))
	m.Score("structure", quality.StructureScore(true, v.frame.Structure, v.recentCHoCH(maxInt(1, v.cfg.ReactionLookbackBars)), v.htfAligned()))
	pip := d.v.cfg.StopBudget.PipSize
	if !(pip > 0) {
		pip = d.v.cfg.PipSize
	}
	roomATR := strategyutil.ScalpRoomATR(ctx.market, atr)
	if !(roomATR > 0) || math.IsInf(roomATR, 0) {
		roomATR = 0
	}
	strategyutil.ScoreRoom(m, strategyutil.MeasureQualityRoom(ctx.market, true, reference, invalidation, target, structural, d.v.cfg.StopBudget, d.v.cfg.Barrier.RoomParams(pip, roomATR), 0), pip)
	if chosen, ok := d.plan.Chosen(); ok {
		m.Measure("entry_depth_fraction", chosen.DepthFraction)
	}
	m.Measure("room_r", d.room.RoomR)
	m.Measure("pd_position", d.inst.PDPosition)
	m.Measure("deep_entry_pips", deepEntryPips(d))
	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}

func boolFloat(v bool) float64 {
	if v {
		return 1
	}
	return 0
}

// deepEntryPips is how far the proximal edge of the entry band moved from the zone's own edge to meet the stop cap (0 when the whole zone fits). Informational.
func deepEntryPips(d *decision) float64 {
	pip := d.v.cfg.StopBudget.PipSize
	if !(pip > 0) {
		pip = d.v.cfg.PipSize
	}
	if !(pip > 0) {
		return 0
	}
	return math.Abs(d.entryHigh-d.res.zone.High()) / pip
}

// zoneSwing is the swing-anchored derivation of the zone as published (nil under the legacy definition).
func zoneSwing(d *decision) *opportunity.ZoneSwing {
	f := d.inst.Formation
	if f == nil {
		return nil
	}
	return &opportunity.ZoneSwing{
		ZoneID: d.id, Class: f.Swing.Class.String(), Strength: f.Strength.String(), AnchorPrice: f.AnchorPrice, ProximalPrice: f.Proximal, DistalPrice: f.Distal,
		SwingConfirmedAt: f.Swing.ConfirmedAt, BaseFormedAt: f.BaseFormedAt, DepartureConfirmedAt: f.DepartureConfirmedAt, BaseCandles: f.BaseEnd - f.BaseStart + 1,
		ProminenceATR: f.Swing.ProminenceATR, DepartureATR: f.DepartureATR, Efficiency: f.Efficiency, External: f.Swing.External, Shift: f.Swing.Shift,
	}
}

// entryPlan is the entry the engine chose from candidate prices inside the zone and how (nil under the proximal entry model).
func entryPlan(d *decision) *opportunity.EntryPlan {
	chosen, ok := d.plan.Chosen()
	if !ok {
		return nil
	}
	plan := &opportunity.EntryPlan{
		Model: "zone_candidates_v1", Kind: chosen.Kind, Price: chosen.Price, DepthFraction: chosen.DepthFraction, StopPips: chosen.StopPips,
		EffectiveRisk: chosen.EffectiveRisk, RoomR: chosen.RoomR, Target: chosen.Target, TargetLabel: chosen.TargetLabel,
	}
	for _, c := range d.plan.Candidates {
		plan.Candidates = append(plan.Candidates, opportunity.EntryPlanCandidate{Kind: c.Kind, Price: c.Price, DepthFraction: c.DepthFraction, Feasible: c.Feasible, Rejected: c.Rejected})
	}
	return plan
}
