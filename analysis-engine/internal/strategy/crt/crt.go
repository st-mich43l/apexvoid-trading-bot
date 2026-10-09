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
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "crt"

// Version is the CRT technical contract version. v3 replaces the frozen
// Python-parity v2 (H1 range swept by any tick, any reclaim, any reaction).
const Version = "v3"

// reactionPattern is the closed reaction pattern a CRT carries downstream:
// the shared vocabulary's sweep-then-reclaim.
const reactionPattern = "sweep_reclaim"

type Strategy struct {
	cfg         Config
	detector    strategyutil.LegacyDetectorSettings
	fingerprint string
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
	_, candidates := s.Analyze(ctx)
	return candidates
}

// Analyze is Evaluate plus the full technical account: every setup, and every
// real sweep episode that a technical, execution-envelope or confluence rule
// refused, with its exact reason.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil {
		return Analysis{}, nil
	}
	m5, h1 := ctx.Timeframes[market.M5], ctx.Timeframes[market.H1]
	if m5 == nil || h1 == nil || len(m5.Candles) == 0 {
		return Analysis{}, nil
	}
	analysis := Detect(s.cfg, Input{H1: h1.Candles, M5: m5.Candles})
	var candidates []opportunity.Candidate
	kept := analysis.Setups[:0:0]
	for _, setup := range analysis.Setups {
		candidate, ok := s.candidate(ctx, setup)
		if !ok {
			analysis.Rejections = append(analysis.Rejections, Rejection{
				Reason: ReasonConfluenceBelowFloor, Direction: setup.Direction, AnchorTime: setup.Anchor.OpenTime, SweepTime: setup.Sweep.BarTime,
			})
			continue
		}
		kept = append(kept, setup)
		candidates = append(candidates, candidate)
	}
	analysis.Setups = kept
	return analysis, candidates
}

// factors scores the shared confluence rubric from MEASURED CRT facts: the
// wick rejection and displacement actually observed, the structure shift that
// actually confirmed, and the higher-timeframe alignment the engine read.
func (s *Strategy) factors(ctx *analysiscontext.MarketContext, setup Setup) confluence.Factors {
	return confluence.Factors{
		HTFAligned:          ctx.Legacy != nil && ctx.Legacy.AlignedWithHTF(setup.Direction),
		WickRejection:       setup.WickRejection,
		StructuralAgreement: setup.Shift != nil,
		DisplacementGrade:   setup.Shift != nil && setup.Shift.DisplacementATR >= s.cfg.DisplacementGradeATR,
	}
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, setup Setup) (opportunity.Candidate, bool) {
	factors := s.factors(ctx, setup)
	score := confluence.Evaluate(confluence.ZoneQuality{}, factors, s.detector.Confluence, 0)
	if score.SelectedStars < s.detector.ConfluenceFloor {
		return opportunity.Candidate{}, false
	}
	side := strings.ToLower(string(setup.Direction))
	structuralID := fmt.Sprintf("technique:crt:%s:%d", side, setup.Anchor.OpenTime)
	evidence := []string{"h1_impulse_range", "m5_range_sweep_reclaim"}
	if setup.Shift != nil {
		evidence = append(evidence, "m5_crt_structure_shift")
	}
	if setup.DoubleRaidResolved {
		evidence = append(evidence, "m5_crt_double_raid_resolved")
	}
	stars := float64(score.SelectedStars)
	components := map[string]float64{
		"confluence":          strategyutil.Clamp01(stars / 3),
		"reaction":            1,
		"h1_range_atr":        setup.Anchor.RangeATR,
		"sweep_depth_atr":     setup.Sweep.DepthATR,
		"reclaim_depth_atr":   setup.Reclaim.DepthATR,
		"technical_rr":        setup.TechnicalRR,
		"technical_risk_pips": setup.RiskPips,
	}
	if setup.Shift != nil {
		components["mss_displacement_atr"] = setup.Shift.DisplacementATR
		components["mss_body_ratio"] = setup.Shift.BodyRatio
		components["mss_close_strength"] = setup.Shift.CloseStrength
	}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version,
		SetupKey: fmt.Sprintf("crt:%s:%d:%d:%d", side, setup.Anchor.OpenTime, setup.Sweep.BarTime, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "crt_sweep_extreme_lost",
		Target: setup.Target, TargetLabel: "opposite_h1_range",
		Evidence: evidence,
		Quality:  opportunity.StrategyQuality{Overall: strategyutil.Clamp01(stars / 3), Components: components},
		FormedAt: setup.Sweep.BarTime, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	// The persistent identity of the thesis is the anchor and side, as it has
	// always been: a later confirmation of the same range is the same thesis.
	candidate.StructuralID = structuralID
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: structuralID, TouchBarTime: setup.Sweep.BarTime, ConfirmationBarTime: setup.ConfirmedAt,
		ReactionType: "rejection", Pattern: reactionPattern,
	}
	candidate.DetectorConfluence = confluenceContext(score)
	return candidate, true
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
