// Package rangeedge restores the frozen Python Range Edge Scalp thesis: price
// at the edge of a confirmed (or provisional / post-impulse) local range, with
// the barrier's own touch and wick-rejection history and a structural reaction
// off the edge. The range itself is the engine's detector-contract scalp
// structure (micro barriers, controlled fallback barrier and explicit range
// states), so the decision, its gates and its confluence qualification are the
// frozen detector's.
package rangeedge

import (
	"fmt"
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "range_edge"
const Version = "v2"

// rangeEdgeDivisor guards the room ratio against a zero ATR.
const rangeEdgeDivisor = 1e-9

type Strategy struct {
	lookback, minimumTouches, minimumWicks, breakCloses int
	minimumRoomATR, invalidationATR, expiryHours        float64
	detector                                            strategyutil.LegacyDetectorSettings
	fingerprint                                         string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("rangeedge: wrong strategy ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*int{"lookback_bars": &s.lookback, "minimum_touches": &s.minimumTouches, "minimum_wick_rejections": &s.minimumWicks, "break_closes": &s.breakCloses} {
		v, err := strategyutil.Int(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	for key, dst := range map[string]*float64{"minimum_room_atr": &s.minimumRoomATR, "invalidation_buffer_atr": &s.invalidationATR, "expiry_hours": &s.expiryHours} {
		v, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	var err error
	if s.detector, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.lookback < 5 || s.minimumTouches < 2 || s.minimumWicks < 1 || s.breakCloses < 1 || s.minimumRoomATR <= 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("rangeedge: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

type edgeCandidate struct {
	direction market.Direction
	barrier   techniquezone.ScalpBarrier
	opposing  float64
}

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	base, ok := strategyutil.NewLegacyDetectorForFrame(ctx, market.M5, s.detector)
	if !ok {
		return nil
	}
	scalp := base.Frame.ScalpRange
	if scalp == nil {
		return nil
	}
	bars := base.Frame.Bars
	last := len(bars) - 1
	baseLookback := maxInt(1, s.detector.ReactionLookbackBars)
	candidates := []edgeCandidate{
		{market.Buy, scalp.Lower, scalp.Upper.Level},
		{market.Sell, scalp.Upper, scalp.Lower.Level},
	}
	sort.SliceStable(candidates, func(i, j int) bool {
		a, b := candidates[i], candidates[j]
		if da, db := math.Abs(a.barrier.Level-base.Price), math.Abs(b.barrier.Level-base.Price); da != db {
			return da < db
		}
		if a.barrier.Score != b.barrier.Score {
			return a.barrier.Score > b.barrier.Score
		}
		return a.direction < b.direction
	})
	for _, c := range candidates {
		d := base.WithDirection(c.direction)
		barrier := c.barrier
		recency := maxInt(0, last-barrier.LastTouchIndex)
		lookback := minInt(maxInt(baseLookback, recency+1), maxInt(baseLookback, s.lookback))
		if !touchedRecently(bars, barrier, lookback) {
			continue
		}
		if barrier.AcceptedCloses >= maxInt(1, s.breakCloses) {
			continue
		}
		zone := barrierZone(barrier, c.direction)
		grab := d.ZoneGrab(zone)
		gradeA := grab != nil && grab.Grade == "A"
		if barrier.Touches < maxInt(2, s.minimumTouches) && !(barrier.Touches >= 2 && gradeA) {
			continue
		}
		if barrier.WickRejections < maxInt(1, s.minimumWicks) && !gradeA {
			continue
		}
		if math.Abs(barrier.Level-scalp.Equilibrium)/math.Max(d.ATR, rangeEdgeDivisor) < math.Max(0, s.minimumRoomATR) {
			continue
		}
		confirmation := d.ReactionWithLookbacks(zone.Low(), zone.High(), grab, lookback, baseLookback)
		if confirmation == nil {
			continue
		}
		factors := confluence.Factors{
			HTFAligned: d.Read.HTFBias == "range" || d.HTFAligned(), Touches: barrier.Touches,
			WickRejection: barrier.WickRejections > 0, DisplacementGrade: gradeA, StructuralAgreement: true,
		}
		// The frozen detector returns _finish's answer for the first edge that
		// passes every gate and reaction, whether or not it qualifies; it does
		// not fall through to the other edge.
		result := d.Finish(barrier.Level, zone, factors, "", nil, nil)
		if result == nil {
			return nil
		}
		return s.candidate(ctx, d, scalp, c, result, confirmation, grab)
	}
	return nil
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, d *strategyutil.LegacyDetector, scalp *techniquezone.ScalpRange, c edgeCandidate, result *strategyutil.LegacyResult, confirmation *strategyutil.LegacyConfirmation, grab *techniquezone.Grab) []opportunity.Candidate {
	barrier := c.barrier
	bars := d.Frame.Bars
	low, high := result.Zone.Low(), result.Zone.High()
	invalid, reference := low-s.invalidationATR*d.ATR, high
	if c.direction == market.Sell {
		invalid, reference = high+s.invalidationATR*d.ATR, low
	}
	_ = reference
	grade := "none"
	if grab != nil {
		grade = grab.Grade
	}
	gradeA := grade == "A"
	quality := strategyutil.Clamp01(.25 + .06*math.Min(barrier.Score, 10) + .15*boolValue(gradeA))
	evidence := []string{"legacy_range_barrier", "barrier_touch_episode", "wick_rejection_history", "legacy_barrier_score", "range_state_" + scalp.State, "accepted_close_valid", "structural_reaction_" + confirmation.Type, "liquidity_grab_grade_" + grade}
	formedAt := bars[barrier.FirstTouchIndex].Time
	if formedAt > confirmation.ConfirmationTime {
		formedAt = confirmation.TouchTime
	}
	setupKey := fmt.Sprintf("range:%s:%d", barrier.Side, bars[barrier.FirstTouchIndex].Time)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: c.direction,
		EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "range_edge_accepted_break",
		Target: scalp.Equilibrium, TargetLabel: "range_equilibrium", Evidence: evidence,
		Quality: opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{
			"touch_history": strategyutil.Clamp01(float64(barrier.Touches) / 4), "wick_history": strategyutil.Clamp01(float64(barrier.WickRejections) / 3),
			"room": strategyutil.Clamp01(math.Abs(barrier.Level-scalp.Equilibrium) / (s.minimumRoomATR * d.ATR)),
		}},
		FormedAt: formedAt, ConfirmedAt: confirmation.ConfirmationTime, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return nil
	}
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: setupKey, TouchBarTime: confirmation.TouchTime, ConfirmationBarTime: confirmation.ConfirmationTime,
		ReactionType: "rejection", Pattern: confirmation.Type,
	}
	candidate.Targets = append(candidate.Targets, opportunity.Target{Price: market.PriceLevel{Price: market.Price(c.opposing), Label: "opposite_range_edge"}})
	candidate.DetectorConfluence = result.ConfluenceContext()
	if candidate.Validate() != nil {
		return nil
	}
	return []opportunity.Candidate{candidate}
}

// barrierZone mirrors _barrier_zone.
func barrierZone(barrier techniquezone.ScalpBarrier, direction market.Direction) techniquezone.Zone {
	side := "supply"
	if direction == market.Buy {
		side = "demand"
	}
	return techniquezone.Zone{
		Bottom: barrier.Low, Top: barrier.High, Side: side, Source: "range_edge", Sources: []string{"range_edge"},
		OriginIndex: -1, BreakIndex: -1, Score: barrier.Score, ScoreReasons: append([]string(nil), barrier.Tags...),
	}
}

// touchedRecently mirrors _barrier_touched_recently: any of the last bars'
// range overlaps the barrier band.
func touchedRecently(bars []market.Candle, barrier techniquezone.ScalpBarrier, count int) bool {
	for _, bar := range bars[maxInt(0, len(bars)-maxInt(1, count)):] {
		if bar.Low <= barrier.High && bar.High >= barrier.Low {
			return true
		}
	}
	return false
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func boolValue(v bool) float64 {
	if v {
		return 1
	}
	return 0
}
