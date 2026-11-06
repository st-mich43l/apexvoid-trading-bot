// Package impulseretrace is Impulse Retrace Scalp (PR-S4): after one forceful M5 displacement bar, the first retrace to the bar's midpoint is bought (or
// sold) in the direction of the impulse, with the shared M1-scalp stop and target book. There is no session, clock or MAD-phase condition anywhere: it
// trades every session. It is a Go-native XAU strategy that owns its whole thesis and shares nothing but the strategyutil scalp helpers, Candidate and
// config parsing. It is evaluated once per closed M5 bar and reads only bars closed at or before that bar. See docs/strategies/impulse_retrace.md.
package impulseretrace

import (
	"fmt"
	"math"
	"strings"
	"time"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "impulse_retrace"
const Version = "v1"

// QualityModel is the version of the ranking score below.
const QualityModel = "impulse_retrace.quality.v1"

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
}

type Strategy struct {
	cfg         Config
	fingerprint string
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
	candidate, built := s.candidate(ctx, m5tf.Session.Active, bars[k], direction, a)
	if !built {
		return nil
	}
	return []opportunity.Candidate{candidate}
}

func (s *Strategy) candidate(ctx *analysiscontext.MarketContext, activeSession string, bar market.Candle, direction market.Direction, atr float64) (opportunity.Candidate, bool) {
	cfg := s.cfg
	pip := cfg.PipSize
	width := cfg.EntryWidthPips * pip
	span := bar.High - bar.Low
	if span <= 0 {
		return opportunity.Candidate{}, false
	}
	var origin, extreme, zoneLow, zoneHigh, worst, structural float64
	if direction == market.Buy {
		origin, extreme = bar.Low, bar.High
		limit := extreme - cfg.RetraceFraction*span
		zoneLow, zoneHigh = limit, limit+width
		worst = zoneHigh
		structural = (worst - (origin - cfg.StopBufferATR*atr)) / pip
	} else {
		origin, extreme = bar.High, bar.Low
		limit := extreme + cfg.RetraceFraction*span
		zoneLow, zoneHigh = limit-width, limit
		worst = zoneLow
		structural = ((origin + cfg.StopBufferATR*atr) - worst) / pip
	}
	// Narrower than the minimum is widened to it; wider than the maximum is refused, never tightened.
	stop, ok := strategyutil.ScalpStopPips(structural, cfg.StopMinimumPips, cfg.StopMaximumPips)
	if !ok {
		return opportunity.Candidate{}, false
	}
	invalidation := worst - stop*pip
	if direction == market.Sell {
		invalidation = worst + stop*pip
	}
	if direction == market.Buy && !(invalidation < zoneLow && zoneLow < zoneHigh) || direction == market.Sell && !(invalidation > zoneHigh && zoneHigh > zoneLow) {
		return opportunity.Candidate{}, false
	}
	// No room cap: the target is the first rung of the ladder that clears the minimum net target.
	targetPrice, _, ok := strategyutil.ScalpTarget(direction, worst, math.Inf(1), stop, cfg.MinimumNetTargetPips, pip, cfg.RewardRiskLadder)
	if !ok {
		return opportunity.Candidate{}, false
	}
	bodyATR := math.Abs(bar.Close-bar.Open) / atr
	side := "buy"
	if direction == market.Sell {
		side = "sell"
	}
	evidence := []string{"m5_impulse_bar", "m5_retrace_limit"}
	if label := strings.ToLower(activeSession); label != "" {
		evidence = append(evidence, "session_"+label) // a label only: nothing reads it
	}
	model := quality.New(QualityModel)
	model.Score("impulse_strength", (bodyATR-cfg.ImpulseBodyATR)/2.0)
	model.Measure("impulse_body_atr", bodyATR)
	model.Measure("retrace_fraction", cfg.RetraceFraction)
	model.Measure("utc_hour", float64(time.Unix(bar.Time, 0).UTC().Hour()))
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("impulse_retrace:%s:%d", side, bar.Time), Symbol: ctx.Symbol, Direction: direction,
		EntryLow: zoneLow, EntryHigh: zoneHigh, Invalidation: invalidation, InvalidationLabel: "impulse_origin_lost",
		Target: targetPrice, TargetLabel: "scalp_ladder", Evidence: evidence,
		Quality:  opportunity.StrategyQuality{Overall: model.Overall(), Components: model.Components()},
		FormedAt: bar.Time, ConfirmedAt: bar.Time, ExpiryHours: (cfg.EntryWindowMinutes + m5Minutes) / 60, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	return candidate, true
}
