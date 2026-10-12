// Package sessionlevel implements the Session Level strategy as an independent Go
// strategy. It owns its own levels: the Asia, London and New York highs and lows and
// the previous trading day's and week's highs and lows, each built from the candles
// of a period that has COMPLETED (a forming session never yields a level) and
// refused when those candles do not really cover the period.
//
// One strategy, two reaction models. A qualified first-touch rejection: a fresh
// approach to a level it has not yet traded through, a touch within tolerance and a
// rejection candle that closes back across the hold threshold within a bounded
// window. A liquidity sweep and reclaim: price trades beyond the level by the sweep
// threshold after the level became available and a rejection candle reclaims it
// within a bounded window; a sustained break is not a reversal. The stop sits beyond
// the level and the reaction's whole adverse extreme (the sweep wick included) and
// the target is the nearest credible opposing liquidity.
//
// See docs/strategies/session_level.md for the specification, the mathematics and
// the intentional differences from the frozen Python publisher this replaced.
package sessionlevel

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

const ID strategy.StrategyID = "session_level"

// Version is the technical contract version. v4 replaces the Python-parity v3 (a
// frame-selected level with a hard-coded touch count, qualified by any reclaim).
const Version = "v4"

type Strategy struct {
	cfg         Config
	detector    strategyutil.LegacyDetectorSettings
	fingerprint string
}

// ParseConfig reads the Session Level parameters; exported so replay and
// certification tooling configure exactly what production does.
func ParseConfig(params map[string]any) (Config, error) { return parseConfig(params) }

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("sessionlevel: wrong strategy ID %q", c.ID)
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

// Analyze is Evaluate plus the full technical account: every level and refused
// period, every episode with its lifecycle state, and the exact reason a confirmed
// reaction was not published. Without closed H1 candles there is no previous day
// or week; the session levels are still built from M5.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil {
		return Analysis{}, nil
	}
	m5 := ctx.Timeframes[market.M5]
	if m5 == nil || len(m5.Candles) == 0 {
		return Analysis{}, nil
	}
	in := Input{M5: m5.Candles}
	if h1 := ctx.Timeframes[market.H1]; h1 != nil {
		in.H1 = h1.Candles
	}
	window, h1, now := prepare(s.cfg, in, len(in.M5)-1)
	built := map[string]opportunity.Candidate{}
	key := func(setup Setup) string {
		return fmt.Sprintf("%s/%s/%s/%d/%d", setup.Direction, setup.Level.ID, setup.Reaction.Model, setup.Reaction.EpisodeAt, setup.ConfirmedAt)
	}
	// The confluence floor is applied while choosing among simultaneous reactions,
	// so a stronger sibling that fails it never hides a weaker one that passes.
	analysis := resolve(detectRaw(s.cfg, window, h1, now), func(setup Setup) bool {
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
// level really received (none claimed when the history cannot show them), the
// rejection wick and force actually observed, the engine's structural direction and
// its higher-timeframe read.
func (s *Strategy) factors(ctx *analysiscontext.MarketContext, m5 *analysiscontext.TimeframeContext, setup Setup) confluence.Factors {
	structural := false
	if m5.Legacy != nil {
		structural = (setup.Direction == market.Buy && m5.Legacy.Structure == "up") || (setup.Direction == market.Sell && m5.Legacy.Structure == "down")
	}
	touches := 0
	if setup.TouchesKnown {
		touches = setup.PriorTouches + 1
	}
	return confluence.Factors{
		HTFAligned:          ctx.Legacy != nil && ctx.Legacy.AlignedWithHTF(setup.Direction),
		Touches:             touches,
		WickRejection:       setup.WickRejection,
		DisplacementGrade:   setup.DisplacementATR >= s.cfg.DisplacementGradeATR,
		StructuralAgreement: structural,
		// A session always applies: the three windows partition the UTC day.
		SessionContext: true,
	}
}

// levelTypeCode encodes the kind of the level for the numeric component map:
// 1 = an intraday session extreme, 2 = the previous day, 3 = the previous week.
func levelTypeCode(kind string) float64 {
	switch kind {
	case KindWeek:
		return 3
	case KindDay:
		return 2
	}
	return 1
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, m5 *analysiscontext.TimeframeContext, setup Setup) (opportunity.Candidate, bool) {
	factors := s.factors(ctx, m5, setup)
	score := confluence.Evaluate(confluence.ZoneQuality{}, factors, s.detector.Confluence, 0)
	if score.SelectedStars < s.detector.ConfluenceFloor {
		return opportunity.Candidate{}, false
	}
	side := strings.ToLower(string(setup.Direction))
	// The thesis is the reaction: this level, this model and this touch or sweep. A
	// later touch of the same level is a separate thesis; a later confirmation of the
	// same reaction is the same one.
	thesis := fmt.Sprintf("technique:session_level:%s:%s:%s:%d", side, setup.Level.ID, setup.Reaction.Model, setup.Reaction.EpisodeAt)

	evidence := []string{"session_level_" + strings.ToLower(setup.Level.Name), "session_level_reaction_confirmed", "session_level_" + setup.Reaction.Model}
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
		"level_type":           levelTypeCode(setup.Level.Kind),
		"level_age_bars":       float64(setup.LevelAgeBars),
		"period_completeness":  setup.Level.Coverage,
		"rejection_strength":   setup.Reaction.RejectionStrength,
		"rejection_wick_ratio": setup.Reaction.WickRatio,
		"reclaim_depth_atr":    setup.Reaction.ReclaimDepthATR,
		"reaction_freshness":   float64(s.freshness(m5, setup)),
		"bars_to_confirm":      float64(setup.Reaction.BarsToConfirm),
		"target_room_r":        setup.RewardRisk,
		"target_room_atr":      setup.TargetRoomATR,
		"level_integrity":      setup.LevelIntegrity,
		"technical_risk_pips":  setup.RiskPips,
		"confluence_stars":     float64(score.SelectedStars),
	}
	// A measured quantity that does not apply to the model is left out, never zeroed:
	// a first touch has no sweep, a sweep has no meaningful penetration, and a touch
	// count the history cannot show is not invented.
	if setup.Reaction.Model == ModelSweepReclaim {
		components["sweep_depth_atr"] = setup.Reaction.SweepDepthATR
	} else {
		components["touch_penetration_atr"] = setup.Reaction.PenetrationATR
	}
	if setup.TouchesKnown {
		components["actual_level_touches"] = float64(setup.PriorTouches + 1)
	}
	if m5.Legacy != nil && (m5.Legacy.Structure == "up" || m5.Legacy.Structure == "down") {
		components["structure_alignment"] = 0
		if factors.StructuralAgreement {
			components["structure_alignment"] = 1
		}
	}
	pattern := "strong_reclaim"
	switch {
	case setup.Reaction.Model == ModelSweepReclaim:
		pattern = "sweep_reclaim"
	case setup.WickRejection:
		pattern = "wick_rejection"
	}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version,
		SetupKey: fmt.Sprintf("session_level:%s:%s:%s:%d:%d", side, setup.Level.ID, setup.Reaction.Model, setup.Reaction.EpisodeAt, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "session_level_invalidated",
		Target: setup.Target, TargetLabel: "opposing_liquidity",
		Evidence: evidence,
		// Overall keeps the scale live arbitration has always seen for this
		// strategy: the confluence stars out of three. The measured quality is in
		// Components.
		Quality:  opportunity.StrategyQuality{Overall: strategyutil.Clamp01(float64(score.SelectedStars) / 3), Components: components},
		FormedAt: setup.Level.AvailableAt, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	candidate.StructuralID = thesis
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: thesis, TouchBarTime: setup.Reaction.EpisodeAt, ConfirmationBarTime: setup.ConfirmedAt,
		ReactionType: "rejection", Pattern: pattern,
	}
	candidate.DetectorConfluence = confluenceContext(score)
	return candidate, true
}

// freshness is how many M5 periods old the confirmation is at the evaluated candle.
func (s *Strategy) freshness(m5 *analysiscontext.TimeframeContext, setup Setup) int {
	last := m5.Candles[len(m5.Candles)-1].Time
	return int((last - setup.ConfirmedAt) / m5Seconds)
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
