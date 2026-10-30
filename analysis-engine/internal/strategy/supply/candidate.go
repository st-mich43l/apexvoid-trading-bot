package supply

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const fixedRewardRisk = "fixed_reward_risk"

const (
	zoneEvidence      = "m5_supply_zone_confirmed"
	confirmedEvidence = "m5_supply_zone_rejection_confirmed"
	invalidationLabel = "supply_zone_invalidated"
	// reactionExtremeEvidence marks a stop that the reaction's own traded extreme set (it lay beyond the whole zone).
	reactionExtremeEvidence = "invalidation_beyond_reaction_extreme"
)

// reactionAnchoredInvalidation puts the stop above the whole zone and above the extreme the bars from the touch to the confirmation traded, plus the buffer;
// anchored is true when the reaction's extreme (not the zone's edge) set it.
func reactionAnchoredInvalidation(bars []market.Candle, conf *confirmation, structHigh, buffer float64) (price float64, anchored bool) {
	structural := structHigh + buffer
	if conf == nil || conf.TouchIndex < 0 || conf.ConfirmationIndex >= len(bars) || conf.TouchIndex > conf.ConfirmationIndex {
		return structural, false
	}
	extreme := math.Inf(-1)
	for _, b := range bars[conf.TouchIndex : conf.ConfirmationIndex+1] {
		extreme = math.Max(extreme, b.High)
	}
	if math.IsInf(extreme, 0) || math.IsNaN(extreme) || !(extreme > structHigh) {
		return structural, false
	}
	return extreme + buffer, true
}

// candidate builds the confirmed opportunity: the published (proximal) entry band, the stop beyond the whole zone and the reaction's extreme, the
// nearest opposing structure (or a fixed reward:risk when none stands in the way) as the target, and the decision's own reaction and confluence as evidence.
func (s *Strategy) candidate(ctx candidateContext, d *decision) (opportunity.Candidate, bool) {
	v := d.v
	atr := v.atr
	low, high := d.res.zone.Low(), d.res.zone.High()
	structLow, structHigh := d.res.structuralLow, d.res.structuralHigh
	reference, invalidation := d.reference, d.invalidation
	target, targetLabel := d.room.Target, d.room.Label
	conf := d.conf
	formedAt, confirmedAt := conf.TouchTime, conf.ConfirmationTime
	setupKey := fmt.Sprintf("zone:%s:rejection:%d:%d", d.id, conf.TouchTime, conf.ConfirmationTime)
	evidence := []string{zoneEvidence, confirmedEvidence}
	if d.inst.Timeframe != "" {
		evidence = append(evidence, "htf_zone_"+lowerTimeframe(d.inst.Timeframe))
	}
	if d.anchored {
		evidence = append(evidence, reactionExtremeEvidence)
	}
	if d.budgetCode != "" {
		evidence = append(evidence, d.budgetCode)
	}
	scored := s.quality(d, reference, invalidation, target, targetLabel != fixedRewardRisk, atr)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.symbol, Direction: market.Sell,
		EntryLow: low, EntryHigh: high, Invalidation: invalidation, InvalidationLabel: invalidationLabel,
		Target: target, TargetLabel: targetLabel, Evidence: evidence, Quality: scored,
		FormedAt: formedAt, ConfirmedAt: confirmedAt, ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	candidate.StructuralID = d.id
	if d.inst.Timeframe != "" {
		candidate.StructureTimeframe = d.inst.Timeframe
	}
	candidate.Structure = s.structureZone(d, structLow, structHigh)
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: d.id, TouchBarTime: conf.TouchTime, ConfirmationBarTime: conf.ConfirmationTime, ReactionType: "rejection", Pattern: conf.Type,
	}
	candidate.DetectorConfluence = d.res.confluenceContext()
	candidate.Provenance = opportunity.AnalysisProvenance{
		StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
		ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
	}
	return candidate, true
}

type candidateContext struct{ symbol market.Symbol }

// structureZone is the whole zone as drawn on its own timeframe (the execution frame is M5), with the open time of the bar that formed it.
func (s *Strategy) structureZone(d *decision, low, high float64) *opportunity.StructureZone {
	timeframe := market.M5
	if d.inst.Timeframe != "" {
		timeframe = d.inst.Timeframe
	}
	formedAt := d.inst.OriginTime
	return &opportunity.StructureZone{Timeframe: timeframe, Low: low, High: high, FormedAt: formedAt}
}

// quality is the technical quality of a confirmed reaction off a supply zone: seven dimensions, each in [0, 1], averaged without weights (informational).
func (s *Strategy) quality(d *decision, reference, invalidation, target float64, structural bool, atr float64) opportunity.StrategyQuality {
	m := quality.New(string(ID) + ".quality.v1")
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
				best = math.Max(best, lo-b.Low)
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
	m.Score("rejection", quality.RejectionScore(v.frame.Bars, d.conf.TouchIndex, d.conf.ConfirmationIndex, false))
	m.Score("entry_location", quality.LocationScore(v.price, reference, atr))
	if atr > 0 {
		m.Measure("entry_chase_atr", math.Abs(v.price-reference)/atr)
	}
	m.Score("structure", quality.StructureScore(false, v.frame.Structure, v.recentCHoCH(maxInt(1, v.cfg.ReactionLookbackBars)), v.htfAligned()))
	room, rewardRisk := quality.GeometryRoom(reference, invalidation, target, structural)
	m.Score("room", room)
	m.Measure("room_reward_risk", rewardRisk)
	m.Measure("room_r", d.room.RoomR)
	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}
