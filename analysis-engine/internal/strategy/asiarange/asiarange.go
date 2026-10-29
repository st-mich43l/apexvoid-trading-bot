// Package asiarange implements Asia Range as an independent Go strategy: gold consolidates in Asia and London either raids one side of that range
// and reverses (a stop run on the range edge) or breaks it and is accepted. It is a complete thesis with its own structure (the finalized Asia
// range as of London open, width-qualified), its own manipulation or acceptance test, its own confirmation and its own stop, the same shape as
// CRT with a session anchor instead of an H1 anchor.
//
// Every fact is read from closed candles only; the Asia range is usable from its AvailableAt. See docs/strategies/asia_range.md.
package asiarange

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

const ID strategy.StrategyID = "asia_range"

// Version is the Asia Range technical contract version.
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
		return nil, fmt.Errorf("asia_range: wrong ID")
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
		return nil, fmt.Errorf("asia_range: %w", err)
	}
	s := &Strategy{cfg: cfg, detector: detector, budget: budget, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	s.budget.Refusals = &s.RefusalCounter
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M5: Asia Range evaluates once per closed M5 candle; the M15 candles and the session levels are read from the context.
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	_, candidates := s.Analyze(ctx)
	return candidates
}

// anchor is the most recent finalized ASIA_H / ASIA_L pair of the session read, usable from its AvailableAt (London open).
func anchor(ctx *analysiscontext.MarketContext, evalClose int64) *RawAnchor {
	var at int64
	for _, level := range ctx.Session.State.Levels {
		if (level.Name == EdgeHigh || level.Name == EdgeLow) && level.Final && level.AvailableAt <= evalClose && level.AvailableAt > at {
			at = level.AvailableAt
		}
	}
	if at == 0 {
		return nil
	}
	raw := &RawAnchor{AvailableAt: at}
	haveHigh, haveLow := false, false
	for _, level := range ctx.Session.State.Levels {
		if !level.Final || level.AvailableAt != at {
			continue
		}
		switch level.Name {
		case EdgeHigh:
			raw.High, haveHigh = float64(level.Price), true
		case EdgeLow:
			raw.Low, haveLow = float64(level.Price), true
		}
	}
	if !haveHigh || !haveLow {
		return nil
	}
	return raw
}

// Analyze is Evaluate plus the full technical account: every setup, and every real episode that a rule refused, with its exact reason.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil || !strategyutil.SymbolAllowed(s.cfg.Symbols, ctx.Symbol) {
		return Analysis{}, nil
	}
	m5, m15 := ctx.Timeframes[market.M5], ctx.Timeframes[market.M15]
	if m5 == nil || m15 == nil || len(m5.Candles) == 0 {
		return Analysis{}, nil
	}
	evalClose := m5.Candles[len(m5.Candles)-1].Time + 300
	in := Input{M5: m5.Candles, M15: m15.Candles, Anchor: anchor(ctx, evalClose), Liquidity: func(direction market.Direction, from, minimum float64) (float64, bool) {
		price, ok := strategyutil.OpposingLiquidity(m5.Liquidity.Pools, direction, from, minimum)
		return float64(price), ok
	}}
	analysis := Detect(s.cfg, in)
	var candidates []opportunity.Candidate
	kept := analysis.Setups[:0:0]
	for _, setup := range analysis.Setups {
		candidate, reason := s.candidate(ctx, setup)
		if reason != "" {
			analysis.Rejections = append(analysis.Rejections, Rejection{Reason: reason, Kind: setup.Kind, Direction: setup.Direction, Edge: setup.Edge, At: setup.ConfirmedAt})
			continue
		}
		kept = append(kept, setup)
		candidates = append(candidates, candidate)
	}
	analysis.Setups = kept
	return analysis, candidates
}

// factors scores the shared confluence rubric from MEASURED facts only. The session-context factor is the swept edge itself: a stop run through the
// finalized Asia high or low is exactly a finalized session level swept and reclaimed.
func (s *Strategy) factors(ctx *analysiscontext.MarketContext, setup Setup) confluence.Factors {
	factors := confluence.Factors{
		HTFAligned:          ctx.Legacy != nil && ctx.Legacy.AlignedWithHTF(setup.Direction),
		WickRejection:       setup.WickRejection,
		StructuralAgreement: setup.Structural,
	}
	if setup.Kind == KindSweepReclaim {
		factors.SessionContext = true
	} else {
		factors.DisplacementGrade = setup.ForceATR >= s.cfg.DisplacementGradeATR
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
	structuralID := fmt.Sprintf("technique:asia_range:%s:%d", strings.ToLower(setup.Edge), setup.Anchor.AvailableAt)
	var evidence []string
	pattern, formed, touch := "sweep_reclaim", setup.SweepTime, setup.SweepTime
	switch setup.Kind {
	case KindSweepReclaim:
		evidence = []string{"m5_asia_range_sweep", "m5_asia_range_reclaim", "m5_asia_range_structure_shift"}
	default:
		evidence = []string{"m15_asia_range_break", "m5_asia_range_acceptance", "m5_asia_range_retest_rejection"}
		pattern, formed, touch = "wick_rejection", setup.BreakTime, setup.RetestTime
		if setup.Engulfing {
			pattern = "engulfing"
		}
	}
	if budgetCode != "" {
		evidence = append(evidence, budgetCode)
	}
	model := quality.New(QualityModel)
	model.Measure("technical_rr", setup.TechnicalRR)
	model.Measure("technical_risk_pips", setup.RiskPips)
	model.Measure("asia_range_atr", setup.Anchor.RangeATR)
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
		SetupKey: fmt.Sprintf("asia_range:%s:%s:%s:%d:%d:%d", setup.Kind, strings.ToLower(setup.Edge), side, setup.Anchor.AvailableAt, formed, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "asia_range_extreme_lost",
		Target: setup.Target, TargetLabel: setup.TargetKind,
		Evidence: evidence,
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: formed, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, ReasonInvalidStopGeometry
	}
	// The persistent identity of the thesis is the edge of that day's range: a later confirmation of the same edge is the same thesis.
	candidate.StructuralID = structuralID
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
