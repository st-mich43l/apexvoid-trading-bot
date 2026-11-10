// Package rangescalp is Range Scalp (PR-S4): inside an M5 range that is genuinely going sideways, a probe of an edge that closes back inside is faded
// at market, with the shared M1-scalp stop and target book. It is a Go-native XAU strategy; it owns its whole thesis (range, sideways filter, probe,
// stop, target, cooldown and daily cap) and shares nothing but the strategyutil scalp helpers, Candidate and config parsing. It is evaluated once per
// closed M5 bar and reads only bars closed at or before that bar. See docs/strategies/range_scalp.md.
package rangescalp

import (
	"fmt"
	"math"
	"sync"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "range_scalp"
const Version = "v2"

// QualityModel is the version of the ranking score below.
const QualityModel = "range_scalp.quality.v2"

// Config is every parameter of the strategy. pip_size and the scalp stop/target book (stop_minimum_pips, stop_maximum_pips, minimum_net_target_pips) are
// injected by the engine for the scalp lane (auto_algo.strategies.scalping.*); trading_day_rollover_utc_hour is the session calendar's.
type Config struct {
	Symbols                          []string
	PipSize                          float64
	StopMinimumPips, StopMaximumPips float64
	MinimumNetTargetPips             float64
	RangeLookbackBars                int
	MaximumEfficiencyRatio           float64
	MinimumRangeATR, MaximumRangeATR float64
	EdgeFraction                     float64
	StopBufferATR                    float64
	EntryWidthPips                   float64
	CooldownBars                     int
	MaximumSignalsPerDay             int
	ExpiryMinutes                    float64
	ATRLength, ATRWindowBars         int
	TradingDayRolloverHour           int
	RewardRiskLadder                 []float64
	// The opposing-barrier policy the room is measured with (SQ-1), injected by the engine from the leaves the Algo Bot's target-room check reads.
	BarrierBufferATR, BarrierMaxWidthATR, BarrierMaxWidthPips float64
}

// Refusals of a real probe, by reason (strategy_refused{strategy="range_scalp", reason}).
const (
	ReasonStopOverCap        = "stop_over_cap"
	ReasonDegenerateGeometry = "degenerate_geometry"
	ReasonNoOpposingRoom     = "no_opposing_room"
	ReasonNoLadderTargetFits = "no_ladder_target_fits_room"
	ReasonBuildFailed        = "candidate_build_failed"
)

// maxCountedRefusals bounds the dedupe memory of the refusal counter: a probe is only re-read for a few bars.
const maxCountedRefusals = 4096

type Strategy struct {
	cfg         Config
	fingerprint string

	// Every refused probe is counted once, by its reason, for the engine's strategy_refused counter (strategy.RefusalReporter).
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
		return nil, fmt.Errorf("range_scalp: wrong ID")
	}
	cfg, err := parseConfig(c.Parameters)
	if err != nil {
		return nil, fmt.Errorf("range_scalp: %w", err)
	}
	return &Strategy{cfg: cfg, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M5: Range Scalp evaluates once per closed M5 bar.
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// signal is one edge probe on a closed bar, before the cooldown and the daily cap.
type signal struct {
	Direction             market.Direction
	Index                 int
	High, Low             float64 // the range
	Width, ATR            float64
	Efficiency            float64
	ProbeDepthATR         float64 // how far the wick went past the edge band, in ATR
	SignalHigh, SignalLow float64 // the signal bar's own extremes
	Close                 float64
	BarTime               int64 // open time of the signal bar
}

// signalAt reads bar k only from bars closed at or before k (the range and the ATR are those of the bars before k).
func (s *Strategy) signalAt(bars []market.Candle, atr *indicator.WilderWindow, k int) (signal, bool) {
	cfg := s.cfg
	lb := cfg.RangeLookbackBars
	if k < lb+1 || k >= len(bars) {
		return signal{}, false
	}
	a, ok := atr.At(k - 1)
	if !ok {
		return signal{}, false
	}
	hi, lo := bars[k-lb].High, bars[k-lb].Low
	for i := k - lb + 1; i < k; i++ {
		hi, lo = math.Max(hi, bars[i].High), math.Min(lo, bars[i].Low)
	}
	width := hi - lo
	if width < cfg.MinimumRangeATR*a || width > cfg.MaximumRangeATR*a {
		return signal{}, false
	}
	path := 0.0
	for i := k - lb + 1; i < k; i++ {
		path += math.Abs(bars[i].Close - bars[i-1].Close)
	}
	if path <= 0 {
		return signal{}, false
	}
	efficiency := math.Abs(bars[k-1].Close-bars[k-lb].Close) / path
	if efficiency >= cfg.MaximumEfficiencyRatio {
		return signal{}, false
	}
	bar := bars[k]
	upper, lower := hi-cfg.EdgeFraction*width, lo+cfg.EdgeFraction*width
	if !(bar.Close > lower && bar.Close < upper) {
		return signal{}, false
	}
	sell, buy := bar.High >= upper, bar.Low <= lower
	if sell == buy {
		// No edge probe, or a bar that probed both edges (no side to fade).
		return signal{}, false
	}
	sig := signal{
		Index: k, High: hi, Low: lo, Width: width, ATR: a, Efficiency: efficiency,
		SignalHigh: bar.High, SignalLow: bar.Low, Close: bar.Close, BarTime: bar.Time,
	}
	if sell {
		sig.Direction, sig.ProbeDepthATR = market.Sell, (bar.High-upper)/a
	} else {
		sig.Direction, sig.ProbeDepthATR = market.Buy, (lower-bar.Low)/a
	}
	return sig, true
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
	sig, ok := s.signalAt(bars, atr, k)
	if !ok {
		return nil
	}
	// Cooldown and daily cap, replayed forward from the start of the current trading day (less one cooldown) so the answer is the same however many bars
	// the context holds: at most one signal per cooldown_bars bars and maximum_signals_per_day per trading day.
	day := strategyutil.TradingDay(bars[k].Time, cfg.TradingDayRolloverHour)
	first := k
	for first > 0 && strategyutil.TradingDay(bars[first-1].Time, cfg.TradingDayRolloverHour) == day {
		first--
	}
	start := first - cfg.CooldownBars
	if start < cfg.RangeLookbackBars+1 {
		start = cfg.RangeLookbackBars + 1
	}
	lastFired, firedToday := -1<<30, 0
	for j := start; j < k; j++ {
		if _, hit := s.signalAt(bars, atr, j); !hit || j-lastFired < cfg.CooldownBars {
			continue
		}
		if strategyutil.TradingDay(bars[j].Time, cfg.TradingDayRolloverHour) == day {
			if firedToday >= cfg.MaximumSignalsPerDay {
				continue
			}
			firedToday++
		}
		lastFired = j
	}
	if k-lastFired < cfg.CooldownBars || firedToday >= cfg.MaximumSignalsPerDay {
		return nil
	}
	candidate, reason := s.candidate(ctx, bars, sig)
	if reason != "" {
		s.countRefusal(reason, bars[k], sig.Direction)
		return nil
	}
	return []opportunity.Candidate{candidate}
}

// candidate builds the opportunity of one probe, or names why it is refused. The room is measured from the worst executable entry to the range's far
// edge or the nearest valid opposing barrier, whichever is nearer (strategyutil.MeasureScalpRoom); the stop is the structural stop and is not touched.
func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, bars []market.Candle, sig signal) (opportunity.Candidate, string) {
	cfg := s.cfg
	pip := cfg.PipSize
	half := cfg.EntryWidthPips * pip / 2
	zoneLow, zoneHigh := sig.Close-half, sig.Close+half
	var worst, structural, objective float64
	if sig.Direction == market.Sell {
		// The stop sits above the probe's extreme or the range edge, whichever is higher, plus the noise buffer.
		stopPrice := math.Max(sig.SignalHigh, sig.High) + cfg.StopBufferATR*sig.ATR
		worst = zoneLow
		structural = (stopPrice - worst) / pip
		objective = sig.Low // the far edge of the range
	} else {
		stopPrice := math.Min(sig.SignalLow, sig.Low) - cfg.StopBufferATR*sig.ATR
		worst = zoneHigh
		structural = (worst - stopPrice) / pip
		objective = sig.High
	}
	// Narrower than the minimum is widened to it; wider than the maximum is refused, never tightened.
	stop, ok := strategyutil.ScalpStopPips(structural, cfg.StopMinimumPips, cfg.StopMaximumPips)
	if !ok {
		return opportunity.Candidate{}, ReasonStopOverCap
	}
	invalidation := worst - stop*pip
	if sig.Direction == market.Sell {
		invalidation = worst + stop*pip
	}
	if sig.Direction == market.Buy && !(invalidation < zoneLow && zoneLow < zoneHigh) || sig.Direction == market.Sell && !(invalidation > zoneHigh && zoneHigh > zoneLow) {
		return opportunity.Candidate{}, ReasonDegenerateGeometry
	}
	room := strategyutil.MeasureScalpRoom(ctx, sig.Direction, worst, objective, 0, strategyutil.ScalpRoomParams{
		PipSize: pip, ATR: strategyutil.ScalpRoomATR(ctx, sig.ATR), BarrierBufferATR: cfg.BarrierBufferATR,
		BarrierMaxWidthATR: cfg.BarrierMaxWidthATR, BarrierMaxWidthPips: cfg.BarrierMaxWidthPips,
	})
	if !(room.Pips > 0) {
		return opportunity.Candidate{}, ReasonNoOpposingRoom
	}
	// The target is the first rung of the ladder that clears the minimum net target AND fits the room. The range midpoint is evidence, not the target.
	targetPrice, targetPips, ok := strategyutil.ScalpTarget(sig.Direction, worst, room.Pips, stop, cfg.MinimumNetTargetPips, pip, cfg.RewardRiskLadder)
	if !ok {
		return opportunity.Candidate{}, ReasonNoLadderTargetFits
	}
	bar := bars[sig.Index]
	side := "buy"
	if sig.Direction == market.Sell {
		side = "sell"
	}
	model := s.score(ctx, sig, worst, stop, targetPips, room)
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("range_scalp:%s:%d", side, bar.Time), Symbol: ctx.Symbol, Direction: sig.Direction,
		EntryLow: zoneLow, EntryHigh: zoneHigh, Invalidation: invalidation, InvalidationLabel: "range_edge_lost",
		Target: targetPrice, TargetLabel: "scalp_ladder", Evidence: []string{"m5_range_sideways", "m5_range_edge_probe", "m5_range_close_inside"},
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: bar.Time, ConfirmedAt: bar.Time, ExpiryHours: cfg.ExpiryMinutes / 60, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, ReasonBuildFailed
	}
	return candidate, ""
}
