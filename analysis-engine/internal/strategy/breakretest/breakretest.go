// Package breakretest implements Break & Retest as an independent Go strategy.
// It owns its own breakout structures, built from closed M5 candles only: a
// horizontal key level made of repeated same-side pivot touches, or a line
// through two pivots evaluated at each candle's own position. A break is
// accepted only after k consecutive closes beyond the structure by a buffer
// and with measured force; the retest must come strictly after the acceptance,
// inside a bounded window, and be confirmed by a directional rejection candle.
// The stop sits beyond the retest and the protected structure, the target is the
// nearest credible opposing structure with room.
//
// See docs/strategies/break_retest.md for the specification, the mathematics and
// the intentional differences from the frozen Python publisher this replaced.
package breakretest

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

const ID strategy.StrategyID = "break_retest"

// Version is the technical contract version. v3 replaces the Python-parity v2
// (a break by any close, the first retest ever, a fixed 0.75 quality).
const Version = "v3"

type Strategy struct {
	cfg         Config
	detector    strategyutil.LegacyDetectorSettings
	fingerprint string
}

// ParseConfig reads the Break & Retest parameters; exported so replay and
// certification tooling configure exactly what production does.
func ParseConfig(params map[string]any) (Config, error) { return parseConfig(params) }

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("breakretest: wrong strategy ID %q", c.ID)
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

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	_, candidates := s.Analyze(ctx)
	return candidates
}

// Analyze is Evaluate plus the full technical account: every break episode, its
// lifecycle state and the exact reason a confirmed retest was not published.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil {
		return Analysis{}, nil
	}
	m5 := ctx.Timeframes[market.M5]
	if m5 == nil || len(m5.Candles) == 0 {
		return Analysis{}, nil
	}
	window := m5.Candles
	if need := s.cfg.requiredHistory(); len(window) > need {
		window = window[len(window)-need:]
	}
	built := map[string]opportunity.Candidate{}
	key := func(setup Setup) string {
		return fmt.Sprintf("%s/%s/%d/%d", setup.Direction, setup.Reference.ID, setup.Break.StartTime, setup.ConfirmedAt)
	}
	// The confluence floor is applied while choosing among references broken in
	// the same move, so a stronger sibling that fails it never hides a weaker one
	// that passes.
	analysis := resolve(detectRaw(s.cfg, window), func(setup Setup) bool {
		candidate, ok := s.candidate(ctx, m5, setup)
		if ok {
			built[key(setup)] = candidate
		}
		return ok
	})
	var candidates []opportunity.Candidate
	for _, setup := range analysis.Setups {
		candidates = append(candidates, built[key(setup)])
	}
	return analysis, candidates
}

// factors scores the shared confluence rubric from MEASURED facts: the touches
// the structure really has, the rejection wick and the break force actually
// observed, the engine's structural direction and its higher-timeframe read.
func (s *Strategy) factors(ctx *analysiscontext.MarketContext, m5 *analysiscontext.TimeframeContext, setup Setup) confluence.Factors {
	structural := false
	if m5.Legacy != nil {
		structural = (setup.Direction == market.Buy && m5.Legacy.Structure == "up") || (setup.Direction == market.Sell && m5.Legacy.Structure == "down")
	}
	return confluence.Factors{
		HTFAligned:          ctx.Legacy != nil && ctx.Legacy.AlignedWithHTF(setup.Direction),
		Touches:             setup.Reference.Touches,
		WickRejection:       setup.WickRejection,
		DisplacementGrade:   setup.Break.DisplacementATR >= s.cfg.DisplacementGradeATR,
		StructuralAgreement: structural,
	}
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, m5 *analysiscontext.TimeframeContext, setup Setup) (opportunity.Candidate, bool) {
	factors := s.factors(ctx, m5, setup)
	score := confluence.Evaluate(confluence.ZoneQuality{}, factors, s.detector.Confluence, 0)
	if score.SelectedStars < s.detector.ConfluenceFloor {
		return opportunity.Candidate{}, false
	}
	side := strings.ToLower(string(setup.Direction))
	thesis := fmt.Sprintf("technique:break_retest:%s:%s:%d", side, setup.Reference.ID, setup.Break.StartTime)

	prefix := "m5_key_level"
	if setup.Reference.Kind == KindTrendline {
		prefix = "m5_trendline"
	}
	evidence := []string{prefix + "_break", prefix + "_retest", "m5_break_accepted", "m5_retest_holds", "m5_retest_rejection"}
	if factors.DisplacementGrade {
		evidence = append(evidence, "displacement_grade")
	}
	if factors.HTFAligned {
		evidence = append(evidence, "htf_aligned")
	}
	if factors.StructuralAgreement {
		evidence = append(evidence, "structural_agreement")
	}

	components := map[string]float64{
		"break_distance_atr":       setup.Break.DistanceATR,
		"break_body_ratio":         setup.Break.BodyRatio,
		"break_close_strength":     setup.Break.CloseStrength,
		"break_displacement_atr":   setup.Break.DisplacementATR,
		"break_accept_closes":      float64(setup.Break.AcceptCloses),
		"retest_depth_atr":         setup.Retest.DepthATR,
		"retest_bars_after_accept": float64(setup.Retest.BarsAfterAcceptance),
		"rejection_wick_ratio":     setup.Retest.WickRatio,
		"rejection_close_strength": setup.Retest.CloseStrength,
		"reference_touches":        float64(setup.Reference.Touches),
		"target_room_atr":          setup.TargetRoomATR,
		"technical_rr":             setup.RewardRisk,
		"technical_risk_pips":      setup.RiskPips,
		"confluence_stars":         float64(score.SelectedStars),
	}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version,
		SetupKey: fmt.Sprintf("break_retest:%s:%s:%d:%d:%d", side, setup.Reference.ID, setup.Break.StartTime, setup.Break.AcceptedAt, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "break_retest_structure_lost",
		Target: setup.Target, TargetLabel: "opposing_structure",
		Evidence: evidence,
		// Overall is the value live arbitration has always seen for this
		// strategy. The measured quality is in Components only.
		Quality:  opportunity.StrategyQuality{Overall: s.cfg.PublishedOverallQuality, Components: components},
		FormedAt: setup.Break.AcceptedAt, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	// The persistent identity of the thesis is the broken structure and its
	// break: a later confirmation of the same retest is the same thesis.
	candidate.StructuralID = thesis
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
