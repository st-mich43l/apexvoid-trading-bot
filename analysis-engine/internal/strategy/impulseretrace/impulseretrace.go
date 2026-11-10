// Package impulseretrace is Impulse Retrace Scalp (PR-S4): after one forceful M5 displacement bar, the first retrace to the bar's midpoint is bought (or
// sold) in the direction of the impulse, with the shared M1-scalp stop and target book. There is no session, clock or MAD-phase condition anywhere: it
// trades every session. It is a Go-native XAU strategy that owns its whole thesis and shares nothing but the strategyutil scalp helpers, Candidate and
// config parsing. It is evaluated once per closed M5 bar and reads only bars closed at or before that bar. See docs/strategies/impulse_retrace.md.
package impulseretrace

import (
	"fmt"
	"math"
	"strings"
	"sync"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "impulse_retrace"
const Version = "v2"

// QualityModel is the version of the ranking score below.
const QualityModel = "impulse_retrace.quality.v2"

// m5Minutes is the duration of the bar the strategy observes: the entry window runs from that bar's close.
const m5Minutes = 5

// cooldownReplayBars bounds how far back the cooldown is replayed (one day of M5 bars), so the answer does not depend on how many bars the context holds.
const cooldownReplayBars = 288

// Config is every parameter of the strategy. pip_size and the scalp stop/target book (stop_minimum_pips, stop_maximum_pips, minimum_net_target_pips) are
// injected by the engine for the scalp lane (auto_algo.strategies.scalping.*).
type Config struct {
	Symbols                          []string
	PipSize                          float64
	StopMinimumPips, StopMaximumPips float64
	MinimumNetTargetPips             float64
	ImpulseBodyATR                   float64
	RetraceFraction                  float64
	StopBufferATR                    float64
	EntryWidthPips                   float64
	EntryWindowMinutes               float64
	CooldownBars                     int
	ATRLength, ATRWindowBars         int
	RewardRiskLadder                 []float64
	// RoomProjection is how far beyond the impulse bar's extreme, as a multiple of the bar's range, the horizon of the room lies when no opposing barrier is
	// nearer: the equal-leg (1.0) measured move of the impulse. The impulse extreme itself is the first objective, not the end of the room: an entry at the
	// retrace with the stop beyond the origin has less than 1R to the extreme by construction.
	RoomProjection float64
	// The opposing-barrier policy the room is measured with (SQ-1), injected by the engine from the leaves the Algo Bot's target-room check reads.
	BarrierBufferATR, BarrierMaxWidthATR, BarrierMaxWidthPips float64
}

// Refusals of a real impulse, by reason (strategy_refused{strategy="impulse_retrace", reason}).
const (
	ReasonStopOverCap        = "stop_over_cap"
	ReasonDegenerateGeometry = "degenerate_geometry"
	ReasonNoOpposingRoom     = "no_opposing_room"
	ReasonNoLadderTargetFits = "no_ladder_target_fits_room"
	ReasonBuildFailed        = "candidate_build_failed"
)

// maxCountedRefusals bounds the dedupe memory of the refusal counter: an impulse is only re-read for a few bars.
const maxCountedRefusals = 4096

type Strategy struct {
	cfg         Config
	fingerprint string

	// Every refused impulse is counted once, by its reason, for the engine's strategy_refused counter (strategy.RefusalReporter).
	mu       sync.Mutex
	refusals map[strategy.Refusal]int64
	counted  map[string]struct{}
}

func (s *Strategy) countRefusal(reason string, bar market.Candle, direction market.Direction) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.counted == nil || len(s.counted) > maxCountedRefusals {
		s.counted = map[string]struct{}{}
	}
	id := fmt.Sprintf("%s|%s|%d", reason, direction, bar.Time)
	if _, seen := s.counted[id]; seen {
		return
	}
	s.counted[id] = struct{}{}
	if s.refusals == nil {
		s.refusals = map[strategy.Refusal]int64{}
	}
	s.refusals[strategy.Refusal{Reason: reason, Timeframe: market.M5}]++
}

// TakeRefusals returns the refusals counted since the last call and resets them (strategy.RefusalReporter).
func (s *Strategy) TakeRefusals() map[strategy.Refusal]int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := s.refusals
	s.refusals = nil
	return out
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("impulse_retrace: wrong ID")
	}
	cfg, err := parseConfig(c.Parameters)
	if err != nil {
		return nil, fmt.Errorf("impulse_retrace: %w", err)
	}
	return &Strategy{cfg: cfg, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M5: Impulse Retrace evaluates once per closed M5 bar.
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// impulseAt reports whether bar k is a forceful displacement bar (its body at least impulse_body_atr times the ATR known before it) and its direction.
func (s *Strategy) impulseAt(bars []market.Candle, atr *indicator.WilderWindow, k int) (market.Direction, float64, bool) {
	if k < 1 || k >= len(bars) {
		return "", 0, false
	}
	a, ok := atr.At(k - 1)
	if !ok {
		return "", 0, false
	}
	body := bars[k].Close - bars[k].Open
	if math.Abs(body) < s.cfg.ImpulseBodyATR*a {
		return "", 0, false
	}
	if body > 0 {
		return market.Buy, a, true
	}
	return market.Sell, a, true
}

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	if ctx == nil || !strategyutil.SymbolAllowed(s.cfg.Symbols, ctx.Symbol) {
		return nil
	}
	m5tf := ctx.Timeframes[market.M5]
	if m5tf == nil || len(m5tf.Candles) == 0 {
		return nil
	}
	cfg := s.cfg
	bars := m5tf.Candles
	k := len(bars) - 1
	atr := indicator.NewWilderWindow(bars, cfg.ATRLength, cfg.ATRWindowBars)
	direction, a, ok := s.impulseAt(bars, atr, k)
	if !ok {
		return nil
	}
	// One impulse per cooldown_bars bars: replay the impulses forward (at most one day back) and let the first of each cluster stand.
	start := k - cooldownReplayBars
	if start < 1 {
		start = 1
	}
	lastFired := -1 << 30
	for j := start; j < k; j++ {
		if _, _, hit := s.impulseAt(bars, atr, j); hit && j-lastFired >= cfg.CooldownBars {
			lastFired = j
		}
	}
	if k-lastFired < cfg.CooldownBars {
		return nil
	}
	candidate, reason := s.candidate(ctx, m5tf.Session.Active, bars[k], direction, a)
	if reason != "" {
		s.countRefusal(reason, bars[k], direction)
		return nil
	}
	return []opportunity.Candidate{candidate}
}

// candidate builds the opportunity of one impulse, or names why it is refused. The room is measured from the planned (worst) entry: the horizon is the
// equal-leg projection of the impulse beyond its extreme, and the nearest valid opposing barrier ahead of the entry limits it when it is nearer
// (strategyutil.MeasureScalpRoom). Barriers born of the impulse itself or later are not its opposing structure. The stop is the structural stop and is not touched.
func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, activeSession string, bar market.Candle, direction market.Direction, atr float64) (opportunity.Candidate, string) {
	cfg := s.cfg
	pip := cfg.PipSize
	width := cfg.EntryWidthPips * pip
	span := bar.High - bar.Low
	if span <= 0 {
		return opportunity.Candidate{}, ReasonDegenerateGeometry
	}
	var origin, extreme, zoneLow, zoneHigh, worst, structural, horizon float64
	if direction == market.Buy {
		origin, extreme = bar.Low, bar.High
		limit := extreme - cfg.RetraceFraction*span
		zoneLow, zoneHigh = limit, limit+width
		worst = zoneHigh
		structural = (worst - (origin - cfg.StopBufferATR*atr)) / pip
		horizon = extreme + cfg.RoomProjection*span
	} else {
		origin, extreme = bar.High, bar.Low
		limit := extreme + cfg.RetraceFraction*span
		zoneLow, zoneHigh = limit-width, limit
		worst = zoneLow
		structural = ((origin + cfg.StopBufferATR*atr) - worst) / pip
		horizon = extreme - cfg.RoomProjection*span
	}
	// Narrower than the minimum is widened to it; wider than the maximum is refused, never tightened.
	stop, ok := strategyutil.ScalpStopPips(structural, cfg.StopMinimumPips, cfg.StopMaximumPips)
	if !ok {
		return opportunity.Candidate{}, ReasonStopOverCap
	}
	invalidation := worst - stop*pip
	if direction == market.Sell {
		invalidation = worst + stop*pip
	}
	if direction == market.Buy && !(invalidation < zoneLow && zoneLow < zoneHigh) || direction == market.Sell && !(invalidation > zoneHigh && zoneHigh > zoneLow) {
		return opportunity.Candidate{}, ReasonDegenerateGeometry
	}
	params := strategyutil.ScalpRoomParams{
		PipSize: pip, ATR: strategyutil.ScalpRoomATR(ctx, atr), BarrierBufferATR: cfg.BarrierBufferATR,
		BarrierMaxWidthATR: cfg.BarrierMaxWidthATR, BarrierMaxWidthPips: cfg.BarrierMaxWidthPips,
	}
	room := strategyutil.MeasureScalpRoom(ctx, direction, worst, horizon, bar.Time, params)
	if !(room.Pips > 0) {
		return opportunity.Candidate{}, ReasonNoOpposingRoom
	}
	// The target is the first rung of the ladder that clears the minimum net target AND fits the room.
	targetPrice, targetPips, ok := strategyutil.ScalpTarget(direction, worst, room.Pips, stop, cfg.MinimumNetTargetPips, pip, cfg.RewardRiskLadder)
	if !ok {
		return opportunity.Candidate{}, ReasonNoLadderTargetFits
	}
	side := "buy"
	if direction == market.Sell {
		side = "sell"
	}
	evidence := []string{"m5_impulse_bar", "m5_retrace_limit"}
	if label := strings.ToLower(activeSession); label != "" {
		evidence = append(evidence, "session_"+label) // a label only: nothing reads it
	}
	extremeRoom := (extreme - worst) / pip
	if direction == market.Sell {
		extremeRoom = (worst - extreme) / pip
	}
	model := s.score(bar, atr, stop, targetPips, extremeRoom, room)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("impulse_retrace:%s:%d", side, bar.Time), Symbol: ctx.Symbol, Direction: direction,
		EntryLow: zoneLow, EntryHigh: zoneHigh, Invalidation: invalidation, InvalidationLabel: "impulse_origin_lost",
		Target: targetPrice, TargetLabel: "scalp_ladder", Evidence: evidence,
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: bar.Time, ConfirmedAt: bar.Time, ExpiryHours: (cfg.EntryWindowMinutes + m5Minutes) / 60, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, ReasonBuildFailed
	}
	return candidate, ""
}
