// Package ifvg implements the Inversion Fair Value Gap as an independent Go
// strategy. It owns its own gap: three consecutive closed M5 candles whose outer
// wicks leave a gap wide enough in ATR, made by a real displacement candle. The
// gap is inverted only by a candle that CLOSES through its far bound by a buffer
// with measured body, close location and displacement; a wick through it is not
// an inversion. The inverted gap then needs a fresh retest strictly after the
// inversion and a directional rejection candle that closes back on the new side.
// The stop sits beyond the gap and the retest's own extreme, and the target is the
// nearest credible opposing swing with room.
//
// See docs/strategies/ifvg.md for the specification, the mathematics and the
// intentional differences from the frozen Python publisher this replaced.
package ifvg

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

const ID strategy.StrategyID = "ifvg"

// Version is the technical contract version. v3 replaces the Python-parity v2 (a
// first close through the gap by any amount, any reaction, a fabricated strength).
const Version = "v3"

type Strategy struct {
	cfg         Config
	detector    strategyutil.LegacyDetectorSettings
	fingerprint string
}

// ParseConfig reads the iFVG parameters; exported so replay and certification
// tooling configure exactly what production does.
func ParseConfig(params map[string]any) (Config, error) { return parseConfig(params) }

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("ifvg: wrong strategy ID %q", c.ID)
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

// Analyze is Evaluate plus the full technical account: every inversion episode,
// its lifecycle state and the exact reason a confirmed rejection was not published.
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
		return fmt.Sprintf("%s/%s/%d/%d", setup.Direction, setup.Gap.ID, setup.Inversion.Time, setup.ConfirmedAt)
	}
	// The confluence floor is applied while choosing among inversions made in the
	// same move, so a stronger sibling that fails it never hides a weaker one that
	// passes.
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

// factors scores the shared confluence rubric from MEASURED facts: the touches the
// inverted gap really received, the rejection wick and inversion force actually
// observed, the engine's structural direction, its higher-timeframe read and the
// session liquidity the move swept.
func (s *Strategy) factors(ctx *analysiscontext.MarketContext, m5 *analysiscontext.TimeframeContext, setup Setup) confluence.Factors {
	structural := false
	if m5.Legacy != nil {
		structural = (setup.Direction == market.Buy && m5.Legacy.Structure == "up") || (setup.Direction == market.Sell && m5.Legacy.Structure == "down")
	}
	return confluence.Factors{
		HTFAligned:          ctx.Legacy != nil && ctx.Legacy.AlignedWithHTF(setup.Direction),
		Touches:             setup.Retest.Touches,
		WickRejection:       setup.WickRejection,
		DisplacementGrade:   setup.Inversion.DisplacementATR >= s.cfg.DisplacementGradeATR,
		StructuralAgreement: structural,
		SessionContext:      s.sessionSweep(ctx, setup),
	}
}

// sessionSweep is the measured session context: a finalized session, previous-day or
// previous-week level that supports the trade was swept and reclaimed during the move that
// built the setup, from the gap's first candle up to the candle BEFORE the retest touch. A
// sweep before the gap existed, one that was not reclaimed, and one on the retest itself (that
// wick IS the rejection, already credited as such) are not session evidence for this inversion.
// The frozen detectors set the factor on every decision.
func (s *Strategy) sessionSweep(ctx *analysiscontext.MarketContext, setup Setup) bool {
	d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, setup.Direction, s.detector)
	if !ok {
		return false
	}
	return d.SessionSweepBetween(setup.Gap.FormedAt-2*m5Seconds, setup.Retest.TouchTime-m5Seconds)
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, m5 *analysiscontext.TimeframeContext, setup Setup) (opportunity.Candidate, bool) {
	factors := s.factors(ctx, m5, setup)
	score := confluence.Evaluate(confluence.ZoneQuality{}, factors, s.detector.Confluence, 0)
	if score.SelectedStars < s.detector.ConfluenceFloor {
		return opportunity.Candidate{}, false
	}
	side := strings.ToLower(string(setup.Direction))
	thesis := fmt.Sprintf("technique:ifvg:%s:%s:%d", side, setup.Gap.ID, setup.Inversion.Time)

	evidence := []string{"m5_ifvg_gap", "m5_ifvg_inversion_confirmed", "m5_ifvg_retest", "m5_ifvg_rejection_confirmed"}
	if factors.DisplacementGrade {
		evidence = append(evidence, "displacement_grade")
	}
	if factors.HTFAligned {
		evidence = append(evidence, "htf_aligned")
	}
	if factors.StructuralAgreement {
		evidence = append(evidence, "structural_agreement")
	}
	if factors.SessionContext {
		evidence = append(evidence, "session_liquidity_swept")
	}

	measurements := map[string]float64{
		"gap_width_atr":              setup.Gap.WidthATR,
		"origin_displacement_atr":    setup.Gap.OriginBodyATR,
		"inversion_body_ratio":       setup.Inversion.BodyRatio,
		"inversion_close_strength":   setup.Inversion.CloseStrength,
		"inversion_displacement_atr": setup.Inversion.DisplacementATR,
		"inversion_close_beyond_atr": setup.Inversion.CloseBeyondATR,
		"bars_gap_to_inversion":      float64(setup.Inversion.BarsFromGap),
		"retest_age_bars":            float64(setup.Retest.BarsAfterInversion),
		"retest_penetration_atr":     setup.Retest.PenetrationATR,
		"rejection_strength":         setup.Retest.CloseStrength,
		"rejection_wick_ratio":       setup.Retest.WickRatio,
		"retest_touches":             float64(setup.Retest.Touches),
		"target_room_r":              setup.RewardRisk,
		"target_room_atr":            setup.TargetRoomATR,
		"zone_integrity":             setup.ZoneIntegrity,
		"technical_risk_pips":        setup.RiskPips,
		"confluence_stars":           float64(score.SelectedStars),
	}
	// structure_alignment is published only when the engine has a structural read:
	// an unavailable fact is left out, never filled with a neutral value.
	if m5.Legacy != nil && (m5.Legacy.Structure == "up" || m5.Legacy.Structure == "down") {
		measurements["structure_alignment"] = 0
		if factors.StructuralAgreement {
			measurements["structure_alignment"] = 1
		}
	}
	model := quality.New(QualityModel)
	for name, value := range measurements {
		model.Measure(name, value)
	}
	qc := qualityContext{htfAligned: factors.HTFAligned}
	if m5.Legacy != nil {
		qc.bars, qc.localTrend = m5.Legacy.Bars, m5.Legacy.Structure
	}
	if d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, setup.Direction, s.detector); ok {
		qc.choch = d.RecentCHoCH(maxInt(1, s.detector.ReactionLookbackBars))
		qc.price = d.Price
	}
	s.score(model, setup, qc)
	pattern := "strong_reclaim"
	if setup.WickRejection {
		pattern = "wick_rejection"
	}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version,
		SetupKey: fmt.Sprintf("ifvg:%s:%s:%d:%d:%d", side, setup.Gap.ID, setup.Inversion.Time, setup.Retest.TouchTime, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "ifvg_inversion_failed",
		Target: setup.Target, TargetLabel: "opposing_structure",
		Evidence: evidence,
		// Overall is the mean of the score_* dimensions (QualityModel); the confluence stars stay in DetectorConfluence as a diagnostic.
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: setup.Inversion.Time, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	// The persistent identity of the thesis is the inverted gap: a later
	// confirmation of the same retest is the same thesis.
	candidate.StructuralID = thesis
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: thesis, TouchBarTime: setup.Retest.TouchTime, ConfirmationBarTime: setup.ConfirmedAt,
		ReactionType: "rejection", Pattern: pattern,
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

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
