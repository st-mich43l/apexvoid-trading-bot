// Package shockretest implements Shock Retest as an independent Go strategy: gold keeps adjusting for longer than five minutes after a volatility
// shock (Awartani, Hussain and Virk 2024; Cai, Cheung and Wong 2001), so the first pullback into the 50 to 61.8 percent retracement of the shock
// leg, rejected in the shock direction, continues toward the shock extreme. The shock is price-defined: the engine has no economic calendar, and
// the strategy behaves identically live and in replay.
//
// Every fact is read from closed M5 candles only. See docs/strategies/shock_retest.md.
package shockretest

import (
	"fmt"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "shock_retest"

// Version is the Shock Retest technical contract version.
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
		return nil, fmt.Errorf("shock_retest: wrong ID")
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
		return nil, fmt.Errorf("shock_retest: %w", err)
	}
	s := &Strategy{cfg: cfg, detector: detector, budget: budget, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	s.budget.Refusals = &s.RefusalCounter
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M5: Shock Retest evaluates once per closed M5 candle.
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	_, candidates := s.Analyze(ctx)
	return candidates
}

// Analyze is Evaluate plus the full technical account: every setup, and every real episode that a rule refused, with its exact reason.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil || !strategyutil.SymbolAllowed(s.cfg.Symbols, ctx.Symbol) {
		return Analysis{}, nil
	}
	m5 := ctx.Timeframes[market.M5]
	if m5 == nil || len(m5.Candles) == 0 {
		return Analysis{}, nil
	}
	analysis := Detect(s.cfg, Input{M5: m5.Candles})
	var candidates []opportunity.Candidate
	kept := analysis.Setups[:0:0]
	for _, setup := range analysis.Setups {
		candidate, reason := s.candidate(ctx, setup)
		if reason != "" {
			analysis.Rejections = append(analysis.Rejections, Rejection{Reason: reason, Direction: setup.Direction, At: setup.ConfirmedAt})
			continue
		}
		kept = append(kept, setup)
		candidates = append(candidates, candidate)
	}
	analysis.Setups = kept
	return analysis, candidates
}

// factors scores the shared confluence rubric from MEASURED facts only: the shock leg's displacement, the rejection wick actually observed and the
// higher-timeframe alignment the engine read.
func (s *Strategy) factors(ctx *analysiscontext.MarketContext, setup Setup) confluence.Factors {
	return confluence.Factors{
		HTFAligned:        ctx.Legacy != nil && ctx.Legacy.AlignedWithHTF(setup.Direction),
		WickRejection:     setup.WickRejection,
		DisplacementGrade: setup.LegATR >= s.cfg.DisplacementGradeATR,
	}
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
	structuralID := fmt.Sprintf("technique:shock_retest:%s:%d", side, setup.OriginTime)
	evidence := []string{"m5_volatility_shock", "m5_shock_first_retest", "m5_shock_retest_rejection"}
	pattern := "wick_rejection"
	if setup.Engulfing {
		pattern = "engulfing"
	}
	if budgetCode != "" {
		evidence = append(evidence, budgetCode)
	}
	model := quality.New(QualityModel)
	model.Measure("technical_rr", setup.TechnicalRR)
	model.Measure("technical_risk_pips", setup.RiskPips)
	model.Measure("shock_leg_atr", setup.LegATR)
	code, momentum, has := strategyutil.SessionMomentumEvidence(ctx, setup.ConfirmedAt, setup.Direction, s.cfg.SessionMomentumMinATR)
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
		SetupKey: fmt.Sprintf("shock_retest:%s:%d:%d:%d", side, setup.OriginTime, setup.ShockTime, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "shock_origin_lost",
		Target: setup.Target, TargetLabel: TargetShockExtreme,
		Evidence: evidence,
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: setup.ShockTime, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, ReasonInvalidStopGeometry
	}
	// The persistent identity of the thesis is the shock: its first candle and side. A later confirmation of the same shock is the same thesis.
	candidate.StructuralID = structuralID
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: structuralID, TouchBarTime: setup.RetestTime, ConfirmationBarTime: setup.ConfirmedAt,
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
