package fvg

import (
	"fmt"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const fixedRewardRisk = "fixed_reward_risk"

const (
	zoneEvidence      = "m5_fvg_zone_confirmed"
	confirmedEvidence = "m5_fvg_rejection_confirmed"
	invalidationLabel = "fvg_invalidated"
	// priorSweepEvidencePrefix is followed by the kind of the liquidity the gap's impulse swept: swing, pdl, pdh or session (measured; sweep.go).
	priorSweepEvidencePrefix = "m5_fvg_prior_sweep_"
	// reactionExtremeEvidence marks a stop that the reaction's own traded extreme set (it lay beyond the whole gap).
	reactionExtremeEvidence = "invalidation_beyond_reaction_extreme"
)

// reactionAnchoredInvalidation puts the stop beyond the whole gap and beyond the extreme the bars from the touch to the confirmation traded, plus the
// buffer; anchored is true when the reaction's extreme (not the gap's edge) set it.
func reactionAnchoredInvalidation(bars []market.Candle, conf *confirmation, direction market.Direction, structLow, structHigh, buffer float64) (price float64, anchored bool) {
	structural := structLow - buffer
	if direction == market.Sell {
		structural = structHigh + buffer
	}
	if conf == nil || conf.TouchIndex < 0 || conf.ConfirmationIndex >= len(bars) || conf.TouchIndex > conf.ConfirmationIndex {
		return structural, false
	}
	if direction == market.Sell {
		extreme := math.Inf(-1)
		for _, b := range bars[conf.TouchIndex : conf.ConfirmationIndex+1] {
			extreme = math.Max(extreme, b.High)
		}
		if math.IsInf(extreme, 0) || math.IsNaN(extreme) || !(extreme > structHigh) {
			return structural, false
		}
		return extreme + buffer, true
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

// candidate builds the confirmed opportunity: the published (proximal) entry band, the stop beyond the whole gap and the reaction's extreme, the
// nearest opposing structure (or a fixed reward:risk when none stands in the way) as the target, and the decision's own reaction and confluence as evidence.
func (s *Strategy) candidate(ctx candidateContext, d *decision) (opportunity.Candidate, bool) {
	v := d.v
	atr := v.atr
	low, high := d.entryLow, d.entryHigh
	structLow, structHigh := d.res.structuralLow, d.res.structuralHigh
	reference, invalidation := d.reference, d.invalidation
	target, targetLabel := d.room.Target, d.room.Label
	conf := d.conf
	formedAt, confirmedAt := conf.TouchTime, conf.ConfirmationTime
	setupKey := fmt.Sprintf("zone:%s:rejection:%d:%d", d.id, conf.TouchTime, conf.ConfirmationTime)
	evidence := []string{zoneEvidence}
	if conf.Type != "" { // the confirmation evidence is published only when a rejecting candle showed it
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
	if d.budgetCode != "" {
		evidence = append(evidence, d.budgetCode)
	}
	if d.sweep.Found {
		evidence = append(evidence, priorSweepEvidencePrefix+d.sweep.Kind)
	}
	if d.hasBreak {
		evidence = append(evidence, breakEvidence(d.brk))
	}
	scored := s.quality(ctx, d, reference, invalidation, target, targetLabel != fixedRewardRisk, atr)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.symbol, Direction: d.direction,
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

// structureZone is the whole gap as drawn on its own timeframe (the execution frame is M5), with the open time of the bar that completed it.
func (s *Strategy) structureZone(d *decision, low, high float64) *opportunity.StructureZone {
	timeframe := market.M5
	if d.inst.Timeframe != "" {
		timeframe = d.inst.Timeframe
	}
	formedAt := d.inst.OriginTime
	return &opportunity.StructureZone{Timeframe: timeframe, Low: low, High: high, FormedAt: formedAt}
}

// quality is the technical quality of a confirmed reaction off a gap: seven dimensions, each in [0, 1], averaged without weights (informational).
func (s *Strategy) quality(ctx candidateContext, d *decision, reference, invalidation, target float64, structural bool, atr float64) opportunity.StrategyQuality {
	m := quality.New(string(ID) + ".quality.v2")
	v := d.v
	buy := d.direction == market.Buy
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
				if buy {
					best = math.Max(best, b.High-hi)
				} else {
					best = math.Max(best, lo-b.Low)
				}
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
	m.Score("rejection", quality.RejectionScore(v.frame.Bars, d.conf.TouchIndex, d.conf.ConfirmationIndex, buy))
	strategyutil.ScoreEntryLocation(m, buy, v.price, reference, atr)
	m.Score("structure", quality.StructureScore(buy, v.frame.Structure, v.recentCHoCH(v.cfg.ConfirmationWindowBars+v.cfg.ConfirmationMaxAgeBars), v.htfAligned()))
	pip := d.v.cfg.StopBudget.PipSize
	if !(pip > 0) {
		pip = d.v.cfg.PipSize
	}
	strategyutil.ScoreRoom(m, strategyutil.MeasureQualityRoom(ctx.market, buy, reference, invalidation, target, structural, d.v.cfg.StopBudget, d.v.cfg.Barrier.RoomParams(pip, strategyutil.ScalpRoomATR(ctx.market, atr)), 0), pip)
	m.Measure("room_r", d.room.RoomR)
	m.Measure("deep_entry_pips", deepEntryPips(d))
	m.Measure("leg_retracement", d.room.Retracement)
	m.Measure("formation_steepness_atr_per_bar", v.steepness(d.inst))
	// The sweep and the structure break are measured facts of the impulse, never scores: nothing reads them to rank or to refuse (the sweep refuses only as
	// the require_prior_sweep gate, before this point).
	m.Measure("prior_sweep", boolMeasure(d.sweep.Found))
	if d.sweep.Found {
		m.Measure("sweep_depth_atr", d.sweep.Depth)
		m.Measure("sweep_reclaimed", boolMeasure(d.sweep.Reclaimed))
	}
	m.Measure("break_kind", breakKindMeasure(d.brk, d.hasBreak))
	return opportunity.StrategyQuality{Overall: m.Overall(), Components: m.Components()}
}

// breakEvidence names the causal structure break by the gap's impulse.
func breakEvidence(brk techniquezone.Break) string {
	if brk.Kind == breakCHoCH {
		return "m5_fvg_break_choch"
	}
	return "m5_fvg_break_bos"
}

// breakKindMeasure is the break_kind quality measure: 1 a BOS, -1 a CHoCH, 0 no break by the impulse.
func breakKindMeasure(brk techniquezone.Break, has bool) float64 {
	switch {
	case !has:
		return 0
	case brk.Kind == breakCHoCH:
		return -1
	}
	return 1
}

func boolMeasure(v bool) float64 {
	if v {
		return 1
	}
	return 0
}

// deepEntryPips is how far the proximal edge of the entry band moved from the gap's own edge to meet the stop cap (0 when the whole gap fits). Informational.
func deepEntryPips(d *decision) float64 {
	pip := d.v.cfg.StopBudget.PipSize
	if !(pip > 0) {
		pip = d.v.cfg.PipSize
	}
	if !(pip > 0) {
		return 0
	}
	if d.direction == market.Sell {
		return math.Abs(d.entryLow-d.res.zone.Low()) / pip
	}
	return math.Abs(d.entryHigh-d.res.zone.High()) / pip
}
