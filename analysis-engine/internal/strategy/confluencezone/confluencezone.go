// Package confluencezone implements Confluence Zone v3: the one compositional
// strategy. It trades a price region that at least minimum_facts genuinely
// independent, already-canonical structures support at the same time, after a
// measured directional rejection of that region. It never invokes another
// strategy and never suppresses one: the structures are neutral zone-chain
// instances, read as facts.
//
// The contract differs from the frozen Python publisher v2 replaced (see
// docs/strategies/confluence-zone-v3-technical-spec.md):
//
//   - two labels are not two facts: structures cut from one displacement, one gap
//     or one inversion are one source (internal/structuralconfluence);
//   - the entry region is where the independent structures overlap, not the union
//     of their bands, and the reaction must interact with that region;
//   - a structure counts only if it was confirmable before the touch candle
//     opened, so one created by the reacting candle (or later) never counts;
//   - the confirmation is measured from the candle (wick, close, displacement,
//     sweep depth) and every quality factor reports a real measurement;
//   - the stop sits beyond the supporting structures and the reaction's own
//     adverse extreme, is refused when it exceeds the execution envelope, and is
//     never tightened;
//   - the target is the nearest opposing liquidity with room, never a made-up
//     reward:risk.
package confluencezone

import (
	"fmt"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structuralconfluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "confluence_zone"

// Version is the technical contract version. v3 replaces the Python-parity v2.
const Version = "v3"

const periodSeconds = 300 // the strategy reads M5

type Strategy struct {
	cfg         Config
	legacy      strategyutil.LegacyDetectorSettings
	fingerprint string
}

// ParseConfig reads the Confluence Zone parameters; exported so replay and
// certification tooling configure exactly what production does.
func ParseConfig(params map[string]any) (Config, error) { return parseConfig(params) }

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("confluencezone: wrong ID")
	}
	cfg, err := parseConfig(c.Parameters)
	if err != nil {
		return nil, err
	}
	legacy, err := strategyutil.ParseLegacyDetectorSettings(c.Parameters)
	if err != nil {
		return nil, err
	}
	return &Strategy{cfg: cfg, legacy: legacy, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Evaluate emits the resting overlap observations and, separately, the confirmed
// reactions. Only a confirmed reaction carries a Reaction, which the execution
// adapter requires; a resting observation is never executable.
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	_, candidates := s.Analyze(ctx)
	return candidates
}

// Analyze is Evaluate plus the full technical account: the structures, the
// supported regions, and the reason every considered setup was not published.
func (s *Strategy) Analyze(ctx *analysiscontext.MarketContext) (Analysis, []opportunity.Candidate) {
	if ctx == nil {
		return Analysis{}, nil
	}
	d, ok := strategyutil.NewLegacyDetectorForFrame(ctx, market.M5, s.legacy)
	if !ok {
		return Analysis{}, nil
	}
	frame := d.Frame
	// The structures are read as facts independent of the current candle and of where
	// price is now: every instance not spent, invalidated, mis-sized or filled, judged
	// on the candles before the one a reaction touched (StructuralInstancesAsOf). A
	// structure the reacting candle swept, filled or closed through is therefore still
	// the structure it was, and the supporting set of one confirmation cannot change
	// between two evaluations. (The frozen publishers' Techniques also require price
	// to be at the zone this moment, and are judged on the present.)
	var factsAt func(asOf int) []structuralconfluence.Fact
	chainFrame := structuralconfluence.Frame{Bars: frame.Bars, Legs: frame.Legs, Breaks: frame.Breaks, Swings: frame.Swings, PeriodSeconds: periodSeconds}
	switch {
	case frame.StructuralInstancesAsOf != nil:
		cache := map[int][]structuralconfluence.Fact{}
		factsAt = func(asOf int) []structuralconfluence.Fact {
			if got, ok := cache[asOf]; ok {
				return got
			}
			got := structuralconfluence.DeriveFacts(chainFrame, frame.StructuralInstancesAsOf(asOf))
			cache[asOf] = got
			return got
		}
	case frame.Techniques != nil:
		present := structuralconfluence.DeriveFacts(chainFrame, frame.Techniques())
		factsAt = func(int) []structuralconfluence.Fact { return present }
	default:
		return Analysis{}, nil
	}
	now := factsAt(len(frame.Bars))
	analysis := Detect(s.cfg, Input{
		Bars: frame.Bars, ATR: d.ATR, PeriodSeconds: periodSeconds, Facts: now, Pools: frame.Pools, Swings: frame.Swings,
		FactsAt: factsAt,
	})

	var candidates []opportunity.Candidate
	for _, resting := range analysis.Resting {
		if candidate, ok := s.restingCandidate(ctx, d, analysis, resting); ok {
			candidates = append(candidates, candidate)
		}
	}
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		var best *built
		for _, setup := range analysis.Setups {
			if setup.Direction != direction {
				continue
			}
			b, reason := s.build(ctx, d, setup)
			if reason != "" {
				analysis.Rejections = append(analysis.Rejections, Rejection{
					Direction: direction, Reason: reason, EpisodeID: setup.Region.EpisodeID, Low: setup.Region.Low, High: setup.Region.High,
					TouchTime: setup.TouchTime, ConfirmTime: setup.ConfirmTime, FactIDs: setup.Region.Support,
				})
				continue
			}
			if best == nil || b.score.SelectedStars > best.score.SelectedStars {
				best = &b
			}
		}
		if best != nil {
			candidates = append(candidates, best.candidate)
		}
	}
	return analysis, candidates
}

type built struct {
	candidate opportunity.Candidate
	score     confluence.Score
}

// factors scores the shared confluence rubric from MEASURED facts only: the
// touches the supporting structures really took, the wick and displacement the
// confirming candle really showed, the engine's own structural direction and
// its higher-timeframe read. Nothing is true because a candidate was emitted.
func (s *Strategy) factors(d *strategyutil.LegacyDetector, setup Setup) confluence.Factors {
	structural := (setup.Direction == market.Buy && d.Frame.Structure == "up") || (setup.Direction == market.Sell && d.Frame.Structure == "down")
	bodyATR := setup.Reaction.BodyRatio * setup.Reaction.RangeATR
	return confluence.Factors{
		HTFAligned:          d.HTFAligned(),
		Touches:             setup.MaxSourceTouches,
		WickRejection:       setup.Reaction.WickRatio >= s.cfg.RejectionWickRatio,
		DisplacementGrade:   bodyATR >= s.cfg.DisplacementGradeATR,
		StructuralAgreement: structural,
		CHoCH:               d.RecentCHoCH(s.cfg.ReactionLookback),
	}
}

func (s *Strategy) build(ctx *analysiscontext.MarketContext, base *strategyutil.LegacyDetector, setup Setup) (built, string) {
	d := base.WithDirection(setup.Direction)
	side := "demand"
	if setup.Direction == market.Sell {
		side = "supply"
	}
	zone := techniquezone.Zone{Bottom: setup.EntryLow, Top: setup.EntryHigh, Side: side, Source: "confluence", BreakIndex: -1, Score: setup.RegionScore, Touches: setup.MaxSourceTouches}
	level := d.ZoneKey(zone)
	if !d.LevelValid(level) || !d.EntryValid(zone) {
		return built{}, ReasonLevelOrEntryInvalid
	}
	factors := s.factors(d, setup)
	result := d.Finish(level, zone, factors, "", nil, nil)
	if result == nil {
		return built{}, ReasonConfluenceFloor
	}
	score := result.Confluence

	components := map[string]float64{
		"independent_fact_count":   float64(setup.IndependentFacts),
		"independent_source_count": float64(len(setup.Region.Groups)),
		"source_dependency_count":  float64(setup.DependencyCount),
		"overlap_width_atr":        setup.OverlapWidthATR,
		"overlap_width_pips":       setup.OverlapWidthPips,
		"overlap_integrity":        setup.OverlapIntegrity,
		"source_integrity":         setup.SourceIntegrity,
		"source_age_bars":          setup.SourceAgeBars,
		"reaction_freshness_bars":  float64(setup.Freshness),
		"rejection_wick_ratio":     setup.Reaction.WickRatio,
		"rejection_close_strength": setup.Reaction.CloseStrength,
		"displacement_strength":    setup.Reaction.BodyRatio * setup.Reaction.RangeATR,
		"reclaim_depth_atr":        setup.Reaction.ReclaimDepthATR,
		"sweep_depth_atr":          setup.Reaction.SweepDepthATR,
		"target_room_r":            setup.RewardRisk,
		"technical_reward_risk":    setup.TechnicalRewardRisk,
		"target_room_atr":          setup.TargetRoomATR,
		"technical_risk_pips":      setup.RiskPips,
		"confluence_stars":         float64(score.SelectedStars),
	}
	// A fact the engine cannot read is left out, never filled with a neutral value.
	if d.Frame.Structure == "up" || d.Frame.Structure == "down" {
		components["structural_alignment"] = boolValue(factors.StructuralAgreement)
	}
	if d.Read.HTFBias == "up" || d.Read.HTFBias == "down" {
		components["htf_alignment"] = boolValue(factors.HTFAligned)
	}

	direction := strings.ToLower(string(setup.Direction))
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version,
		SetupKey: fmt.Sprintf("confluence:%s:%d", setup.Region.EpisodeID, setup.ConfirmTime),
		Symbol:   ctx.Symbol, Direction: setup.Direction,
		EntryLow: setup.EntryLow, EntryHigh: setup.EntryHigh,
		Invalidation: setup.Stop, InvalidationLabel: "confluence_overlap_failed",
		Target: setup.Target, TargetLabel: "nearest_opposing_liquidity",
		Evidence: []string{"m5_distinct_zone_overlap", "m5_confluence_independent_facts", "m5_confluence_reaction"},
		// Overall keeps the scale live arbitration has always seen for this
		// strategy: the confluence stars out of three. The measured quality is in
		// Components.
		Quality:  opportunity.StrategyQuality{Overall: strategyutil.Clamp01(float64(score.SelectedStars) / 3), Components: components},
		FormedAt: setup.TouchTime, ConfirmedAt: setup.ConfirmTime,
		ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return built{}, "candidate_invalid:" + direction
	}
	// The persistent identity of the thesis is the confluence episode (the side
	// and the structures that support it), not the bar that confirmed it: a
	// re-confirmation of the same episode is the same thesis.
	candidate.StructuralID = setup.Region.EpisodeID
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: setup.Region.EpisodeID, TouchBarTime: setup.TouchTime, ConfirmationBarTime: setup.ConfirmTime,
		ReactionType: "rejection", Pattern: setup.Reaction.Pattern,
	}
	candidate.DetectorConfluence = result.ConfluenceContext()
	return built{candidate: candidate, score: score}, ""
}

func (s *Strategy) restingCandidate(ctx *analysiscontext.MarketContext, d *strategyutil.LegacyDetector, analysis Analysis, r Resting) (opportunity.Candidate, bool) {
	bar, ok := strategyutil.LastBar(ctx, market.M5)
	if !ok {
		return opportunity.Candidate{}, false
	}
	formed := int64(0)
	for _, f := range analysis.Facts {
		for _, id := range r.Region.Support {
			if f.ID == id && f.FormedAt > formed {
				formed = f.FormedAt
			}
		}
	}
	if formed == 0 || formed > bar.Time {
		formed = bar.Time
	}
	q := strategyutil.Clamp01(float64(r.Region.MinGroups) / 4)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: "confluence:overlap:" + r.Region.EpisodeID,
		Symbol: ctx.Symbol, Direction: r.Direction, EntryLow: r.EntryLow, EntryHigh: r.EntryHigh,
		Invalidation: r.Stop, InvalidationLabel: "confluence_overlap_failed", Target: r.Target, TargetLabel: "nearest_opposing_liquidity",
		// A resting observation claims no reaction: its evidence is the overlap of
		// independent structures only.
		Evidence: []string{"m5_distinct_zone_overlap", "m5_confluence_independent_facts"},
		Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{
			"independent_fact_count": float64(r.Region.MinGroups), "overlap_width_atr": r.Region.Width() / r.ATR,
		}},
		FormedAt: formed, ConfirmedAt: bar.Time, ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	candidate.StructuralID = r.Region.EpisodeID
	return candidate, true
}

func boolValue(v bool) float64 {
	if v {
		return 1
	}
	return 0
}
