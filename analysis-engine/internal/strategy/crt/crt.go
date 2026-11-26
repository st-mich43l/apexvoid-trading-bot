// Package crt implements Candle Range Theory as an independent Go strategy: a
// fully closed H1 candle defines a range, a later M5 candle sweeps one edge
// and an M5 close reclaims it, a subsequent M5 structure shift confirms the
// reversal, and the setup is invalidated beyond the manipulation extreme with
// the opposite H1 edge as its technical objective.
//
// Every fact is read from closed candles only and related across timeframes by
// time, never by slice position. See docs/strategies/crt.md for the full
// specification, the mathematics, and the intentional differences from the
// frozen Python publisher this strategy replaced.
package crt

import (
	"fmt"
	"sort"
	"strings"
	"sync"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "crt"

// Version is the CRT technical contract version. v3 replaced the frozen
// Python-parity v2 (H1 range swept by any tick, any reclaim, any reaction). v4 (PR-CRT4,
// owner 26 Nov 2026): anchors on M15 and H1, and entry_model reclaim_leg (the reclaim leg as
// the entry zone, the opposite edge as the objective on the effective risk, candle 2's close
// rule through the published anchor structure, a per-frame expiry). With entry_model
// reclaim_retest and structure_timeframes [H1] the output is v3's apart from the version.
const Version = "v4"

// reactionPattern is the closed reaction pattern a CRT carries downstream:
// the shared vocabulary's sweep-then-reclaim.
const reactionPattern = "sweep_reclaim"

type Strategy struct {
	cfg         Config
	detector    strategyutil.LegacyDetectorSettings
	fingerprint string

	// Every refused sweep episode is counted once, by its reason, for the engine's strategy_refused counter (strategy.RefusalReporter). Analyze always knew the
	// reasons; without this the engine never saw them and a quiet CRT looked the same as a market with no sweeps.
	mu       sync.Mutex
	refusals map[strategy.Refusal]int64
	counted  map[string]struct{}
}

const maxCountedRefusals = 4096

// refusedNoAnchorFrames is counted when the context has no closed H1 or M5 candles to read an anchor from.
const refusedNoAnchorFrames = "no_h1_or_m5_frames"

func (s *Strategy) countRefusal(reason string, id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.counted == nil || len(s.counted) > maxCountedRefusals {
		s.counted = map[string]struct{}{}
	}
	key := reason + "|" + id
	if _, seen := s.counted[key]; seen {
		return
	}
	s.counted[key] = struct{}{}
	if s.refusals == nil {
		s.refusals = map[strategy.Refusal]int64{}
	}
	s.refusals[strategy.Refusal{Reason: reason, Timeframe: refusalTimeframe}]++
}

// refusalTimeframe is the structure_timeframe label of CRT refusals: kept M5 (v3's label) so the counter's series is continuous; the anchor frame of a refused
// episode is part of its identity (countRefusal's id) instead.
const refusalTimeframe = market.M5

// TakeRefusals returns the refusals counted since the last call and resets them (strategy.RefusalReporter).
func (s *Strategy) TakeRefusals() map[strategy.Refusal]int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.refusals
	s.refusals = nil
	return out
}

// ParseConfig reads the CRT parameters; exported so replay and certification
// tooling configure exactly what production does.
func ParseConfig(params map[string]any) (Config, error) { return parseConfig(params) }

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("crt: wrong ID")
	}
	cfg, err := parseConfig(c.Parameters)
	if err != nil {
		return nil, err
	}
	detector, err := strategyutil.ParseLegacyDetectorSettings(c.Parameters)
	if err != nil {
		return nil, err
	}
	return &Strategy{cfg: cfg, detector: detector, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M5: CRT evaluates once per closed M5 candle. The H1
// anchor is read from the context's closed H1 candles when present; without
// them there is no anchor and no setup.
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	analysis, candidates := s.Analyze(ctx)
	if ctx == nil {
		return candidates
	}
	if m5, h1 := ctx.Timeframes[market.M5], ctx.Timeframes[s.cfg.StructureTimeframes[0]]; m5 == nil || h1 == nil || len(m5.Candles) == 0 {
		var now int64
		if m5 != nil && len(m5.Candles) > 0 {
			now = m5.Candles[len(m5.Candles)-1].Time
		}
		s.countRefusal(refusedNoAnchorFrames, fmt.Sprint(now))
	}
	for _, rejection := range analysis.Rejections {
		s.countRefusal(string(rejection.Reason), fmt.Sprintf("%s|%s|%d|%d", rejection.Timeframe, rejection.Direction, rejection.AnchorTime, rejection.SweepTime))
	}
	return candidates
}

// Analyze is Evaluate plus the full technical account: every setup, and every
// real sweep episode that a technical, execution-envelope or confluence rule
// refused, with its exact reason.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil {
		return Analysis{}, nil
	}
	m5 := ctx.Timeframes[market.M5]
	if m5 == nil || len(m5.Candles) == 0 {
		return Analysis{}, nil
	}
	// One detection per anchor frame (v4: M15 and H1; v3: H1). Sweep, reclaim and structure shift are always read on M5.
	var analysis Analysis
	type kept struct {
		setup     Setup
		candidate opportunity.Candidate
	}
	var found []kept
	for _, tf := range s.cfg.StructureTimeframes {
		frame := ctx.Timeframes[tf]
		if frame == nil {
			continue
		}
		one := Detect(s.cfg, Input{H1: frame.Candles, AnchorTF: tf, M5: m5.Candles})
		analysis.EvaluatedAt = one.EvaluatedAt
		analysis.Anchors += one.Anchors
		analysis.Rejections = append(analysis.Rejections, one.Rejections...)
		for _, setup := range one.Setups {
			candidate, reason := s.candidate(ctx, setup)
			if reason != "" {
				analysis.Rejections = append(analysis.Rejections, Rejection{
					Timeframe: tf, Reason: reason, Direction: setup.Direction, AnchorTime: setup.Anchor.OpenTime, SweepTime: setup.Sweep.BarTime,
				})
				continue
			}
			found = append(found, kept{setup, candidate})
		}
	}
	// One candidate per thesis across frames: the same direction confirmed by the same M5 structure shift is one trade idea, whatever range it was read
	// against. The higher timeframe is kept (strategyutil.PreferHigherTimeframe), then the later confirmation; the others are counted duplicate_thesis.
	sort.SliceStable(found, func(i, j int) bool {
		a, b := found[i].setup, found[j].setup
		if a.Anchor.Timeframe != b.Anchor.Timeframe {
			return strategyutil.PreferHigherTimeframe(strategyutil.ThesisRank{Timeframe: a.Anchor.Timeframe}, strategyutil.ThesisRank{Timeframe: b.Anchor.Timeframe})
		}
		return a.ConfirmedAt > b.ConfirmedAt
	})
	var candidates []opportunity.Candidate
	taken := map[string]market.Timeframe{}
	for _, f := range found {
		thesis := fmt.Sprintf("%s|%d", f.setup.Direction, f.setup.ConfirmedAt)
		// Only a thesis already taken on ANOTHER frame is a duplicate: two anchors of one frame confirmed by one candle published both in v3 and still do.
		if tf, seen := taken[thesis]; seen && tf != f.setup.Anchor.Timeframe {
			analysis.Rejections = append(analysis.Rejections, Rejection{
				Timeframe: f.setup.Anchor.Timeframe, Reason: ReasonDuplicateThesis, Direction: f.setup.Direction, AnchorTime: f.setup.Anchor.OpenTime, SweepTime: f.setup.Sweep.BarTime,
			})
			continue
		}
		if _, seen := taken[thesis]; !seen {
			taken[thesis] = f.setup.Anchor.Timeframe
		}
		analysis.Setups = append(analysis.Setups, f.setup)
		candidates = append(candidates, f.candidate)
	}
	return analysis, candidates
}

// factors scores the shared confluence rubric from MEASURED CRT facts: the wick
// rejection actually observed on the sweep, the structure shift that actually
// confirmed, and the higher-timeframe alignment the engine read. The shift's
// displacement is not a second fact: it is a quality of the same candle that made the
// shift (and is already required of it, MinimumMSSDisplacement), so it earns no extra
// displacement credit; it is published as a component instead.
func (s *Strategy) factors(ctx *analysiscontext.MarketContext, setup Setup) confluence.Factors {
	return confluence.Factors{
		HTFAligned:          ctx.Legacy != nil && ctx.Legacy.AlignedWithHTF(setup.Direction),
		WickRejection:       setup.WickRejection,
		StructuralAgreement: setup.Shift != nil,
	}
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, setup Setup) (opportunity.Candidate, string) {
	factors := s.factors(ctx, setup)
	score := confluence.Evaluate(confluence.ZoneQuality{}, factors, s.detector.Confluence, 0)
	if score.SelectedStars < s.detector.ConfluenceFloor {
		return opportunity.Candidate{}, ReasonConfluenceBelowFloor
	}
	side := strings.ToLower(string(setup.Direction))
	anchorTF := strings.ToLower(string(setup.Anchor.Timeframe))
	structuralID := fmt.Sprintf("technique:crt:%s:%d", side, setup.Anchor.OpenTime)
	if setup.Anchor.Timeframe != market.H1 { // an H1 anchor keeps v3's identity
		structuralID = fmt.Sprintf("technique:crt:%s:%s:%d", anchorTF, side, setup.Anchor.OpenTime)
	}
	evidence := []string{anchorTF + "_impulse_range", "m5_range_sweep_reclaim"}
	if setup.Shift != nil {
		evidence = append(evidence, "m5_crt_structure_shift")
	}
	if setup.DoubleRaidResolved {
		evidence = append(evidence, "m5_crt_double_raid_resolved")
	}
	if s.cfg.v4() {
		evidence = append(evidence, "crt_anchor_tf_"+anchorTF, "crt_entry_reclaim_leg", "crt_range_midpoint")
		if setup.Candle2ClosedInside {
			evidence = append(evidence, "crt_candle2_closed_inside")
		}
		if setup.HasObstacle {
			evidence = append(evidence, "crt_nearest_obstacle")
		}
		if setup.StopBudgetCode != "" {
			evidence = append(evidence, setup.StopBudgetCode)
		}
	}
	measurements := map[string]float64{
		"h1_range_atr":        setup.Anchor.RangeATR,
		"sweep_depth_atr":     setup.Sweep.DepthATR,
		"reclaim_depth_atr":   setup.Reclaim.DepthATR,
		"technical_rr":        setup.TechnicalRR,
		"technical_risk_pips": setup.RiskPips,
	}
	if setup.Shift != nil {
		measurements["mss_displacement_atr"] = setup.Shift.DisplacementATR
		measurements["mss_body_ratio"] = setup.Shift.BodyRatio
		measurements["mss_close_strength"] = setup.Shift.CloseStrength
	}
	model := quality.New(QualityModel)
	for name, value := range measurements {
		model.Measure(name, value)
	}
	qc := qualityContext{htfAligned: factors.HTFAligned}
	if m5 := ctx.Timeframes[market.M5]; m5 != nil && m5.Legacy != nil {
		qc.localTrend = m5.Legacy.Structure
	}
	if d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, setup.Direction, s.detector); ok {
		qc.choch, qc.price = setupCHoCH(d, setup), d.Price
	}
	if s.cfg.v4() {
		model.Measure("crt_range_midpoint", setup.Anchor.Mid())
		model.Measure("effective_risk_pips", setup.EffectiveRiskPips)
		if setup.HasObstacle {
			model.Measure("crt_nearest_obstacle", setup.NearestObstacle)
		}
	}
	s.score(model, setup, qc)
	expiryHours := s.cfg.ExpiryHours
	if s.cfg.v4() {
		// v4: at most expiry_hours after the confirmation, and never later than expiry_after_candle2_periods periods of the anchor frame after candle 2
		// closed. An opportunity whose window has already passed by its publication is refused, not published dead.
		expiresAt := setup.ConfirmedAt + int64(s.cfg.ExpiryHours*3600)
		if periods := s.cfg.ExpiryAfterCandle2Periods[setup.Anchor.Timeframe]; periods > 0 {
			minutes, _ := setup.Anchor.Timeframe.Minutes()
			if cap := setup.Candle2Close + int64(periods*minutes*60); cap < expiresAt {
				expiresAt = cap
			}
		}
		if expiresAt <= setup.ConfirmedAt+m5Seconds {
			return opportunity.Candidate{}, ReasonExpiredBeforePublication
		}
		expiryHours = float64(expiresAt-setup.ConfirmedAt) / 3600
	}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version,
		SetupKey: fmt.Sprintf("crt:%s:%d:%d:%d", side, setup.Anchor.OpenTime, setup.Sweep.BarTime, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "crt_sweep_extreme_lost",
		Target: setup.Target, TargetLabel: setup.TargetKind,
		Evidence: evidence,
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: setup.Sweep.BarTime, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: expiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, ReasonConfluenceBelowFloor
	}
	// The persistent identity of the thesis is the anchor and side, as it has
	// always been: a later confirmation of the same range is the same thesis.
	candidate.StructuralID = structuralID
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: structuralID, TouchBarTime: setup.Sweep.BarTime, ConfirmationBarTime: setup.ConfirmedAt,
		ReactionType: "rejection", Pattern: reactionPattern,
	}
	candidate.DetectorConfluence = confluenceContext(score)
	if s.cfg.v4() {
		// Candle 2's close rule (v4): the anchor range is the published structure on the anchor frame, so a close of THAT frame beyond the swept edge
		// (structure_close_buffer_atr 0.0: any close) withdraws the opportunity (STRUCTURE_CLOSED_THROUGH, the engine's structure-close rule).
		candidate.Structure = &opportunity.StructureZone{Timeframe: setup.Anchor.Timeframe, Low: setup.Anchor.Low, High: setup.Anchor.High, FormedAt: setup.Anchor.CloseTime}
		candidate.StructureTimeframe = setup.Anchor.Timeframe
	}
	return candidate, ""
}

func confluenceContext(score confluence.Score) *opportunity.ConfluenceContext {
	return &opportunity.ConfluenceContext{
		Version: score.Version, SelectedStars: score.SelectedStars, V1Stars: score.V1Stars, V2Stars: score.V2Stars,
		V2Raw: score.V2Raw, RawFactorScore: score.RawFactorScore, ZoneQualityScore: score.ZoneQualityScore, MADBonus: score.MADBonus,
		Factors: opportunity.ConfluenceFactors{
			HTFAligned: score.Factors.HTFAligned, Touches: score.Factors.Touches, WickRejection: score.Factors.WickRejection,
			DisplacementGrade: score.Factors.DisplacementGrade, SessionContext: score.Factors.SessionContext,
			StructuralAgreement: score.Factors.StructuralAgreement, FibTouch: score.Factors.FibTouch, CHoCH: score.Factors.CHoCH,
		},
	}
}
