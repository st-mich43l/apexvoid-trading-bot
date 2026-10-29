// Package roundnumber implements Round Number as an independent Go strategy for gold handles: stop orders cluster just beyond a round number and
// take-profit orders cluster at it (Osler 2003/2005), so a trade through the stop band that is immediately taken back is a stop run to fade, and a
// break that is accepted triggers a cascade whose retest is traded in the break direction.
//
// Every fact is read from closed M5 candles only. See docs/strategies/round_number.md for the specification, the evidence and what differs from
// key_level, which trades a round level only when swings already reacted there.
package roundnumber

import (
	"fmt"
	"math"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "round_number"

// Version is the Round Number technical contract version.
const Version = "v1"

type Strategy struct {
	strategyutil.RefusalCounter
	cfg         Config
	detector    strategyutil.LegacyDetectorSettings
	budget      strategyutil.StopBudget
	fingerprint string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("round_number: wrong ID")
	}
	cfg, err := parseConfig(c.Parameters)
	if err != nil {
		return nil, err
	}
	detector, err := strategyutil.ParseLegacyDetectorSettings(c.Parameters)
	if err != nil {
		return nil, err
	}
	budget, err := strategyutil.ParseStopBudget(c.Parameters)
	if err != nil {
		return nil, fmt.Errorf("round_number: %w", err)
	}
	s := &Strategy{cfg: cfg, detector: detector, budget: budget, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	s.budget.Refusals = &s.RefusalCounter
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M5: Round Number evaluates once per closed M5 candle.
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	_, candidates := s.Analyze(ctx)
	return candidates
}

// Analyze is Evaluate plus the full technical account: every setup, and every real episode that a technical, execution-envelope or confluence
// rule refused, with its exact reason.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil || !strategyutil.SymbolAllowed(s.cfg.Symbols, ctx.Symbol) {
		return Analysis{}, nil
	}
	m5 := ctx.Timeframes[market.M5]
	if m5 == nil || len(m5.Candles) == 0 {
		return Analysis{}, nil
	}
	in := Input{M5: m5.Candles, Liquidity: func(direction market.Direction, from, minimum float64) (float64, bool) {
		price, ok := strategyutil.OpposingLiquidity(m5.Liquidity.Pools, direction, from, minimum)
		return float64(price), ok
	}}
	analysis := Detect(s.cfg, in)
	var candidates []opportunity.Candidate
	kept := analysis.Setups[:0:0]
	for _, setup := range analysis.Setups {
		candidate, reason := s.candidate(ctx, setup)
		if reason != "" {
			analysis.Rejections = append(analysis.Rejections, Rejection{Reason: reason, Kind: setup.Kind, Direction: setup.Direction, Handle: setup.Handle, At: setup.ConfirmedAt})
			continue
		}
		kept = append(kept, setup)
		candidates = append(candidates, candidate)
	}
	analysis.Setups = kept
	return s.selectSiblings(ctx, analysis, candidates)
}

// selectSiblings keeps one candidate among those confirmed on the same bar (house rule: more stars, then the entry nearest the last close).
func (s *Strategy) selectSiblings(ctx *analysiscontext.MarketContext, analysis Analysis, candidates []opportunity.Candidate) (Analysis, []opportunity.Candidate) {
	if len(candidates) < 2 {
		return analysis, candidates
	}
	last := ctx.Timeframes[market.M5].Candles[len(ctx.Timeframes[market.M5].Candles)-1].Close
	nearest := func(c opportunity.Candidate) float64 {
		return math.Min(math.Abs(float64(c.Entry.Low)-last), math.Abs(float64(c.Entry.High)-last))
	}
	best := map[int64]int{}
	for i, c := range candidates {
		at := analysis.Setups[i].ConfirmedAt
		j, seen := best[at]
		if !seen || c.DetectorConfluence.SelectedStars > candidates[j].DetectorConfluence.SelectedStars ||
			c.DetectorConfluence.SelectedStars == candidates[j].DetectorConfluence.SelectedStars && nearest(c) < nearest(candidates[j]) {
			best[at] = i
		}
	}
	var keptSetups []Setup
	var keptCandidates []opportunity.Candidate
	for i, c := range candidates {
		if best[analysis.Setups[i].ConfirmedAt] != i {
			analysis.Rejections = append(analysis.Rejections, Rejection{Reason: ReasonSupersededBySibling, Kind: analysis.Setups[i].Kind, Direction: analysis.Setups[i].Direction, Handle: analysis.Setups[i].Handle, At: analysis.Setups[i].ConfirmedAt})
			continue
		}
		keptSetups, keptCandidates = append(keptSetups, analysis.Setups[i]), append(keptCandidates, c)
	}
	analysis.Setups = keptSetups
	return analysis, keptCandidates
}

// factors scores the shared confluence rubric from MEASURED facts only: the rejection wick actually observed, the structure that actually moved,
// the force of the break, the higher-timeframe alignment the engine read and a finalized session level swept in the same window.
func (s *Strategy) factors(ctx *analysiscontext.MarketContext, setup Setup) confluence.Factors {
	factors := confluence.Factors{
		HTFAligned:          ctx.Legacy != nil && ctx.Legacy.AlignedWithHTF(setup.Direction),
		WickRejection:       setup.WickRejection,
		StructuralAgreement: setup.Structural,
	}
	if setup.Kind == KindBreakRetest {
		factors.DisplacementGrade = setup.ForceATR >= s.cfg.DisplacementGradeATR
	}
	if d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, setup.Direction, s.detector); ok {
		from := setup.SweepTime
		if setup.Kind == KindBreakRetest {
			from = setup.BreakTime
		}
		factors.SessionContext = d.SessionSweepBetween(from, setup.ConfirmedAt+300)
	}
	return factors
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, setup Setup) (opportunity.Candidate, string) {
	factors := s.factors(ctx, setup)
	score := confluence.Evaluate(confluence.ZoneQuality{}, factors, s.detector.Confluence, 0)
	if score.SelectedStars < s.detector.ConfluenceFloor {
		return opportunity.Candidate{}, ReasonConfluenceBelowFloor
	}
	reference := setup.EntryHigh
	if setup.Direction == market.Sell {
		reference = setup.EntryLow
	}
	budgetCode, publish := s.budget.Apply(reference, setup.Stop)
	if !publish {
		return opportunity.Candidate{}, ReasonRiskExceedsEnvelope
	}
	side := strings.ToLower(string(setup.Direction))
	structuralID := fmt.Sprintf("technique:round_number:%s:%.2f:%d", side, setup.Handle, setup.Day)
	var evidence []string
	pattern := "sweep_reclaim"
	formed := setup.SweepTime
	switch setup.Kind {
	case KindSweepReclaim:
		evidence = []string{"m5_round_number_sweep", "m5_round_number_reclaim", "m5_round_number_structure_shift"}
	default:
		evidence = []string{"m5_round_number_break", "m5_round_number_acceptance", "m5_round_number_retest_rejection"}
		pattern, formed = "wick_rejection", setup.BreakTime
		if setup.Engulfing {
			pattern = "engulfing"
		}
	}
	if setup.Major {
		evidence = append(evidence, "round_number_major_handle")
	}
	if budgetCode != "" {
		evidence = append(evidence, budgetCode)
	}
	model := quality.New(QualityModel)
	model.Measure("technical_rr", setup.TechnicalRR)
	model.Measure("technical_risk_pips", setup.RiskPips)
	code, momentum, has := strategyutil.SessionMomentumEvidence(ctx, setup.ConfirmedAt, setup.Direction, s.cfg.SessionMomentumMinimumATR)
	if has {
		model.Measure("session_momentum_atr", momentum)
	}
	if code != "" {
		evidence = append(evidence, code)
	}
	qc := qualityContext{htfAligned: factors.HTFAligned}
	if m5 := ctx.Timeframes[market.M5]; m5 != nil {
		if m5.Legacy != nil {
			qc.localTrend = m5.Legacy.Structure
		}
		if n := len(m5.Candles); n > 0 {
			qc.price = m5.Candles[n-1].Close
		}
	}
	if d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, setup.Direction, s.detector); ok {
		qc.choch = d.RecentCHoCH(maxInt(1, s.detector.ReactionLookbackBars))
	}
	s.score(model, setup, qc)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version,
		SetupKey: fmt.Sprintf("round_number:%s:%s:%.2f:%d:%d", setup.Kind, side, setup.Handle, formed, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "round_number_extreme_lost",
		Target: setup.Target, TargetLabel: setup.TargetKind,
		Evidence: evidence,
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: formed, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, ReasonInvalidStopGeometry
	}
	// The persistent identity of the thesis is the handle, side and trading day: re-observing it on a later bar is the same thesis.
	candidate.StructuralID = structuralID
	touch := setup.SweepTime
	if setup.Kind == KindBreakRetest {
		touch = setup.RetestTime
	}
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: structuralID, TouchBarTime: touch, ConfirmationBarTime: setup.ConfirmedAt,
		ReactionType: "rejection", Pattern: pattern,
	}
	candidate.DetectorConfluence = confluenceContext(score)
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

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
