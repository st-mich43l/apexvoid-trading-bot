// Package rangescalp is Range Scalp (PR-S4): inside an M5 range that is genuinely going sideways, a probe of an edge that closes back inside is faded
// at market, with the shared M1-scalp stop and target book. It is a Go-native XAU strategy; it owns its whole thesis (range, sideways filter, probe,
// stop, target, cooldown and daily cap) and shares nothing but the strategyutil scalp helpers, Candidate and config parsing. It is evaluated once per
// closed M5 bar and reads only bars closed at or before that bar. See docs/strategies/range_scalp.md.
package rangescalp

import (
	"fmt"
	"math"
	"time"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "range_scalp"
const Version = "v1"

// QualityModel is the version of the ranking score below.
const QualityModel = "range_scalp.quality.v1"

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
}

type Strategy struct {
	cfg         Config
	fingerprint string
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
		SignalHigh: bar.High, SignalLow: bar.Low, Close: bar.Close,
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
	candidate, built := s.candidate(ctx, bars, sig)
	if !built {
		return nil
	}
	return []opportunity.Candidate{candidate}
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, bars []market.Candle, sig signal) (opportunity.Candidate, bool) {
	cfg := s.cfg
	pip := cfg.PipSize
	half := cfg.EntryWidthPips * pip / 2
	zoneLow, zoneHigh := sig.Close-half, sig.Close+half
	var worst, structural float64
	if sig.Direction == market.Sell {
		// The stop sits above the probe's extreme or the range edge, whichever is higher, plus the noise buffer.
		stopPrice := math.Max(sig.SignalHigh, sig.High) + cfg.StopBufferATR*sig.ATR
		worst = zoneLow
		structural = (stopPrice - worst) / pip
	} else {
		stopPrice := math.Min(sig.SignalLow, sig.Low) - cfg.StopBufferATR*sig.ATR
		worst = zoneHigh
		structural = (worst - stopPrice) / pip
	}
	// Narrower than the minimum is widened to it; wider than the maximum is refused, never tightened.
	stop, ok := strategyutil.ScalpStopPips(structural, cfg.StopMinimumPips, cfg.StopMaximumPips)
	if !ok {
		return opportunity.Candidate{}, false
	}
	invalidation := worst - stop*pip
	if sig.Direction == market.Sell {
		invalidation = worst + stop*pip
	}
	if sig.Direction == market.Buy && !(invalidation < zoneLow && zoneLow < zoneHigh) || sig.Direction == market.Sell && !(invalidation > zoneHigh && zoneHigh > zoneLow) {
		return opportunity.Candidate{}, false
	}
	// No room cap: the target is the first rung of the ladder that clears the minimum net target. The range midpoint is evidence, not the target.
	targetPrice, _, ok := strategyutil.ScalpTarget(sig.Direction, worst, math.Inf(1), stop, cfg.MinimumNetTargetPips, pip, cfg.RewardRiskLadder)
	if !ok {
		return opportunity.Candidate{}, false
	}
	bar := bars[sig.Index]
	side := "buy"
	if sig.Direction == market.Sell {
		side = "sell"
	}
	model := quality.New(QualityModel)
	model.Score("sideways", 1-sig.Efficiency/cfg.MaximumEfficiencyRatio)
	model.Score("edge_probe", sig.ProbeDepthATR)
	model.Measure("range_width_atr", sig.Width/sig.ATR)
	model.Measure("efficiency_ratio", sig.Efficiency)
	model.Measure("range_midpoint", (sig.High+sig.Low)/2)
	model.Measure("range_high", sig.High)
	model.Measure("range_low", sig.Low)
	model.Measure("utc_hour", float64(time.Unix(bar.Time, 0).UTC().Hour()))
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("range_scalp:%s:%d", side, bar.Time), Symbol: ctx.Symbol, Direction: sig.Direction,
		EntryLow: zoneLow, EntryHigh: zoneHigh, Invalidation: invalidation, InvalidationLabel: "range_edge_lost",
		Target: targetPrice, TargetLabel: "scalp_ladder", Evidence: []string{"m5_range_sideways", "m5_range_edge_probe", "m5_range_close_inside"},
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: bar.Time, ConfirmedAt: bar.Time, ExpiryHours: cfg.ExpiryMinutes / 60, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	return candidate, true
}
