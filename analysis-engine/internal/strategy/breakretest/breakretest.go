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
	"math"
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

const ID strategy.StrategyID = "break_retest"

// Version is the technical contract version. v3 replaced the Python-parity v2
// (a break by any close, the first retest ever, a fixed 0.75 quality); v4 reads the
// broken structure on M5, M15 or H1 and the retest on M5.
const Version = "v4"

// Refusals of Break & Retest (v4), by reason (strategy_refused{strategy="break_retest", structure_timeframe, reason}), counted once per episode: the retest was touched
// and no M5 rejection confirmed it within the window ("no_m5_reaction"), the reason a confirmed retest was not published (the Reason* constants), or the same broken
// reference qualified on more than one frame ("duplicate_thesis").
const (
	RefusedNoM5Reaction    = "no_m5_reaction"
	RefusedDuplicateThesis = "duplicate_thesis"
)

type Strategy struct {
	mu          sync.Mutex
	refusals    map[strategy.Refusal]int64
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

func (s *Strategy) refuse(tf market.Timeframe, reason string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.refusals == nil {
		s.refusals = map[strategy.Refusal]int64{}
	}
	s.refusals[strategy.Refusal{Reason: reason, Timeframe: tf}]++
}

// TakeRefusals returns the refusals counted since the last call and resets them (strategy.RefusalReporter).
func (s *Strategy) TakeRefusals() map[strategy.Refusal]int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.refusals
	s.refusals = nil
	return out
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	_, candidates := s.Analyze(ctx)
	return candidates
}

// published is one candidate a frame produced, with what the thesis order needs.
type published struct {
	frame     market.Timeframe
	setup     Setup
	candidate opportunity.Candidate
}

// Analyze is Evaluate plus the full technical account: every break episode of every listed frame, its lifecycle state and the exact reason a confirmed retest was not
// published. For each listed frame the broken reference, the break and its acceptance and the protected structure are read on that frame's closed bars and the retest
// and its rejection on M5; one candidate per thesis is published (the same broken reference found on several frames is one thesis: the higher frame, then more stars,
// then the nearer entry).
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil {
		return Analysis{}, nil
	}
	m5 := ctx.Timeframes[market.M5]
	if m5 == nil || len(m5.Candles) == 0 {
		return Analysis{}, nil
	}
	var total Analysis
	var found []published
	for _, tf := range s.cfg.StructureTimeframes {
		structure := m5.Candles
		if tf != market.M5 {
			frame := ctx.Timeframes[tf]
			if frame == nil || len(frame.Candles) == 0 {
				continue
			}
			structure = frame.Candles
		}
		window := structure
		if need := s.cfg.requiredHistory(); len(window) > need {
			window = window[len(window)-need:]
		}
		minutes, _ := tf.Minutes()
		period := int64(minutes) * 60
		fine := window
		if tf != market.M5 {
			// The M5 bars from the start of the structure window: the retest of any episode of the window lies in them.
			first := sort.Search(len(m5.Candles), func(i int) bool { return m5.Candles[i].Time >= window[0].Time })
			fine = m5.Candles[first:]
		}
		if len(fine) == 0 {
			continue
		}
		raw := detectRaw(s.cfg, window, period, fine)
		for i := range raw {
			raw[i].Frame = tf
			if raw[i].Setup != nil {
				raw[i].Setup.Frame = tf
			}
		}
		built := map[string]opportunity.Candidate{}
		key := func(setup Setup) string {
			return fmt.Sprintf("%s/%s/%d/%d", setup.Direction, setup.Reference.ID, setup.Break.StartTime, setup.ConfirmedAt)
		}
		// The confluence floor is applied while choosing among references broken in the same move on this frame, so a stronger sibling that fails it never hides a
		// weaker one that passes.
		analysis := resolve(raw, func(setup Setup) bool {
			candidate, ok := s.candidate(ctx, m5, setup, tf)
			if ok {
				built[key(setup)] = candidate
			}
			return ok
		})
		s.countRefusals(analysis.Episodes, tf, fine[len(fine)-1].Time)
		for _, setup := range analysis.Setups {
			found = append(found, published{frame: tf, setup: setup, candidate: built[key(setup)]})
		}
		total.Setups = append(total.Setups, analysis.Setups...)
		total.Episodes = append(total.Episodes, analysis.Episodes...)
	}
	var candidates []opportunity.Candidate
	for _, p := range s.selectTheses(found, m5.Candles[len(m5.Candles)-1].Close) {
		candidates = append(candidates, p.candidate)
	}
	return total, candidates
}

// countRefusals counts, once per episode, the retests that did not become a setup as of the latest M5 bar: a touched retest whose rejection window ran out, and a
// confirmed retest that was not published, by the reason it was not.
func (s *Strategy) countRefusals(episodes []Episode, tf market.Timeframe, latest int64) {
	for _, ep := range episodes {
		if ep.Setup != nil || ep.EndedAt != latest {
			continue
		}
		switch {
		case ep.State == StateExpired && ep.Reason == ReasonConfirmWindowExpired:
			s.refuse(tf, RefusedNoM5Reaction)
		case ep.State == StateRetestConfirmed && ep.Reason != "":
			s.refuse(tf, ep.Reason)
		}
	}
}

// selectTheses keeps one candidate per thesis: the same direction and a broken reference within the wider of the two structure-frame cluster bands, found on
// different frames, are one thesis, decided by strategyutil.PreferHigherTimeframe (higher frame, then more stars, then the nearer entry). The ones it drops are counted
// as duplicate_thesis. A frame's own candidates are never merged with each other (that is resolve's per-direction rule).
func (s *Strategy) selectTheses(found []published, price float64) []published {
	rank := func(p published) strategyutil.ThesisRank {
		stars := 0
		if p.candidate.DetectorConfluence != nil {
			stars = p.candidate.DetectorConfluence.SelectedStars
		}
		return strategyutil.ThesisRank{Timeframe: p.frame, Stars: stars, EntryDistance: math.Abs(price - (p.setup.EntryLow+p.setup.EntryHigh)/2)}
	}
	same := func(a, b published) bool {
		return a.frame != b.frame && a.setup.Direction == b.setup.Direction &&
			math.Abs(a.setup.Reference.PriceAtBreak-b.setup.Reference.PriceAtBreak) <= math.Max(a.setup.ThesisBand, b.setup.ThesisBand)
	}
	dropped := make([]bool, len(found))
	for i := range found {
		for j := i + 1; j < len(found); j++ {
			if dropped[i] || dropped[j] || !same(found[i], found[j]) {
				continue
			}
			if strategyutil.PreferHigherTimeframe(rank(found[j]), rank(found[i])) {
				dropped[i] = true
				s.refuse(found[i].frame, RefusedDuplicateThesis)
			} else {
				dropped[j] = true
				s.refuse(found[j].frame, RefusedDuplicateThesis)
			}
		}
	}
	var out []published
	for i, p := range found {
		if !dropped[i] {
			out = append(out, p)
		}
	}
	return out
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

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, m5 *analysiscontext.TimeframeContext, setup Setup, tf market.Timeframe) (opportunity.Candidate, bool) {
	factors := s.factors(ctx, m5, setup)
	score := confluence.Evaluate(confluence.ZoneQuality{}, factors, s.detector.Confluence, 0)
	if score.SelectedStars < s.detector.ConfluenceFloor {
		return opportunity.Candidate{}, false
	}
	side := strings.ToLower(string(setup.Direction))
	frame := strings.ToLower(string(tf))
	thesis := fmt.Sprintf("technique:break_retest:%s:%s:%s:%d", frame, side, setup.Reference.ID, setup.Break.StartTime)

	prefix := "m5_key_level"
	if setup.Reference.Kind == KindTrendline {
		prefix = "m5_trendline"
	}
	evidence := []string{"structure_tf_" + frame, prefix + "_break", prefix + "_retest", "m5_break_accepted", "m5_retest_holds"}
	if setup.Rejected {
		evidence = append(evidence, "m5_retest_rejection")
	} else {
		evidence = append(evidence, strategyutil.CandleConfirmationOptionalEvidence)
	}
	if factors.DisplacementGrade {
		evidence = append(evidence, "displacement_grade")
	}
	if factors.HTFAligned {
		evidence = append(evidence, "htf_aligned")
	}
	if factors.StructuralAgreement {
		evidence = append(evidence, "structural_agreement")
	}

	measurements := map[string]float64{
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
	model := quality.New(QualityModel)
	for name, value := range measurements {
		model.Measure(name, value)
	}
	qc := qualityContext{htfAligned: factors.HTFAligned, pipSize: s.cfg.PipSize}
	qc.room = s.qualityRoom(ctx, setup)
	if m5.Legacy != nil {
		qc.localTrend = m5.Legacy.Structure
	}
	if d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, setup.Direction, s.detector); ok {
		qc.choch, qc.price = d.RecentCHoCH(maxInt(1, s.detector.ReactionLookbackBars)), d.Price
	}
	s.score(model, setup, qc)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version,
		SetupKey: fmt.Sprintf("break_retest:%s:%s:%s:%d:%d:%d", frame, side, setup.Reference.ID, setup.Break.StartTime, setup.Break.AcceptedAt, setup.ConfirmedAt),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "break_retest_structure_lost",
		Target: setup.Target, TargetLabel: "opposing_structure",
		Evidence: evidence,
		// Overall is the mean of the score_* dimensions (QualityModel); the confluence stars stay in DetectorConfluence as a diagnostic.
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: setup.Break.AcceptedAt, ConfirmedAt: setup.ConfirmedAt,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	// The persistent identity of the thesis is the broken structure and its
	// break: a later confirmation of the same retest is the same thesis.
	candidate.StructuralID = thesis
	// The structure behind the setup is the broken reference's retest band on its own frame: a close of THAT frame beyond its far edge withdraws the opportunity
	// (the engine's structure-close rule, structure_close_buffer_atr).
	candidate.StructureTimeframe = tf
	candidate.Structure = &opportunity.StructureZone{Timeframe: tf, Low: setup.EntryLow, High: setup.EntryHigh, FormedAt: setup.Break.AcceptedAt}
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
