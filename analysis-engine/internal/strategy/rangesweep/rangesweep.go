// Package rangesweep implements the Range Sweep Scalp: the Python
// discover_range_sweep of the profitable XAU week (14–18 Sep 2026), on the Go
// analysis authority.
//
// Thesis. The setup lives on M5 and is executed on M1: price has spent the last
// 24 closed M5 bars inside a range at least 25 pips wide; a closed M1 bar then
// touches or pierces one edge within the stop buffer and closes back inside it
// in the trade direction (BUY at the lower edge, SELL at the upper), and the
// dealing-range position of price agrees with the edge (BUY only in the lower
// 35 %, SELL only in the upper 35 %). The entry zone is the edge widened by the
// scalp buffer, the stop is structural beyond the sweep extreme, the target is
// 1:2 else 1:1 inside the corridor room, as for every scalp of that lane.
//
// Stateless and evaluated on every closed M1 bar, exactly as the Python lane
// re-ran on every closed M1. Spread-dependent guards stay execution-owned. See
// strategyutil/scalpcontext.go for the shared context mechanics.
package rangesweep

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "range_sweep"
const Version = "v3"

// Config is every tunable of the strategy (analysis.strategies.range_sweep, the
// shared auto_algo.strategies.scalping book injected per instrument).
type Config struct {
	Symbols []string

	PipSize float64

	SetupWindowBars        int
	DealingWindowBars      int
	ConfirmationWindowBars int
	ContextMaxAgeSeconds   int64
	ContextATRBars         int
	ActiveRangeBars        int
	ExpiryMinutes          float64

	MinimumRangeWidthPips float64
	BuyMaximumPosition    float64
	SellMinimumPosition   float64
	TriggerMaximumAgeBars int

	BufferM1ATRMultiple     float64
	BufferMinSpreadMultiple float64
	MaximumSpreadPips       float64
	StopMinimumPips         float64
	StopMaximumPips         float64
	MinimumNetTargetPips    float64
	RewardRiskLadder        []float64
}

type Strategy struct {
	cfg         Config
	fingerprint string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("rangesweep: wrong ID")
	}
	cfg, err := parseConfig(c.Parameters)
	if err != nil {
		return nil, fmt.Errorf("rangesweep: %w", err)
	}
	return &Strategy{cfg: cfg, fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}, nil
}

func parseConfig(params map[string]any) (Config, error) {
	var cfg Config
	var err error
	num := func(key string) float64 {
		if err != nil {
			return 0
		}
		var v float64
		v, err = strategyutil.Float(params, key)
		return v
	}
	count := func(key string) int {
		if err != nil {
			return 0
		}
		var v int
		v, err = strategyutil.Int(params, key)
		return v
	}
	cfg.PipSize = num("pip_size")
	cfg.SetupWindowBars = count("setup_window_bars")
	cfg.DealingWindowBars = count("dealing_window_bars")
	cfg.ConfirmationWindowBars = count("confirmation_window_bars")
	cfg.ContextMaxAgeSeconds = int64(count("context_max_age_seconds"))
	cfg.ContextATRBars = count("context_atr_bars")
	cfg.ActiveRangeBars = count("active_range_bars")
	cfg.ExpiryMinutes = num("expiry_minutes")
	cfg.MinimumRangeWidthPips = num("minimum_range_width_pips")
	cfg.BuyMaximumPosition = num("buy_maximum_position")
	cfg.SellMinimumPosition = num("sell_minimum_position")
	cfg.TriggerMaximumAgeBars = count("trigger_maximum_age_bars")
	cfg.BufferM1ATRMultiple = num("buffer_m1_atr_multiple")
	cfg.BufferMinSpreadMultiple = num("buffer_minimum_spread_multiple")
	cfg.MaximumSpreadPips = num("maximum_spread_pips")
	cfg.StopMinimumPips = num("stop_minimum_pips")
	cfg.StopMaximumPips = num("stop_maximum_pips")
	cfg.MinimumNetTargetPips = num("minimum_net_target_pips")
	if err != nil {
		return Config{}, err
	}
	ladder, ok := params["reward_risk_ladder"].([]any)
	if !ok || len(ladder) == 0 {
		return Config{}, fmt.Errorf("reward_risk_ladder must be a non-empty list")
	}
	for _, item := range ladder {
		switch v := item.(type) {
		case float64:
			cfg.RewardRiskLadder = append(cfg.RewardRiskLadder, v)
		case int:
			cfg.RewardRiskLadder = append(cfg.RewardRiskLadder, float64(v))
		default:
			return Config{}, fmt.Errorf("reward_risk_ladder entries must be numbers")
		}
	}
	if raw, present := params["symbols"]; present {
		list, ok := raw.([]any)
		if !ok {
			return Config{}, fmt.Errorf("symbols must be a list")
		}
		for _, item := range list {
			name, ok := item.(string)
			if !ok {
				return Config{}, fmt.Errorf("symbols entries must be strings")
			}
			cfg.Symbols = append(cfg.Symbols, name)
		}
	}
	switch {
	case cfg.PipSize <= 0, cfg.SetupWindowBars < 10, cfg.DealingWindowBars < 5, cfg.ConfirmationWindowBars < 15, cfg.ContextATRBars < 1,
		cfg.ActiveRangeBars < 2, cfg.ExpiryMinutes <= 0, cfg.MinimumRangeWidthPips < 0, cfg.TriggerMaximumAgeBars < 1,
		cfg.StopMinimumPips <= 0, cfg.StopMaximumPips < cfg.StopMinimumPips,
		cfg.BuyMaximumPosition < 0 || cfg.BuyMaximumPosition > 1, cfg.SellMinimumPosition < 0 || cfg.SellMinimumPosition > 1:
		return Config{}, fmt.Errorf("invalid parameters")
	}
	return cfg, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M1 only: the Python lane ran once per closed M1 bar (M5
// and M15 are read from the context), and evaluating again on the M5 close would
// observe the same candidate at an earlier bar time than the M1 observation
// already recorded.
func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{market.M1}
}

func (s *Strategy) allowsSymbol(symbol market.Symbol) bool {
	if len(s.cfg.Symbols) == 0 {
		return true
	}
	for _, allowed := range s.cfg.Symbols {
		if allowed == string(symbol) {
			return true
		}
	}
	return false
}

func closedBy(bars []market.Candle, closeAt int64, minutes int64, window int) []market.Candle {
	end := len(bars)
	for end > 0 && bars[end-1].Time+minutes*60 > closeAt {
		end--
	}
	bars = bars[:end]
	if window > 0 && len(bars) > window {
		bars = bars[len(bars)-window:]
	}
	return bars
}

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	if ctx == nil || !s.allowsSymbol(ctx.Symbol) {
		return nil
	}
	m5tf, m1tf := ctx.Timeframes[market.M5], ctx.Timeframes[market.M1]
	if m5tf == nil || m1tf == nil || len(m5tf.Candles) == 0 || len(m1tf.Candles) == 0 {
		return nil
	}
	cfg := s.cfg
	m1All := m1tf.Candles
	now := m1All[len(m1All)-1].Time
	closeAt := now + 60
	m5 := closedBy(m5tf.Candles, closeAt, 5, cfg.SetupWindowBars)
	m1 := m1All
	if len(m1) > cfg.ConfirmationWindowBars {
		m1 = m1[len(m1)-cfg.ConfirmationWindowBars:]
	}
	if len(m5) == 0 || len(m1) == 0 {
		return nil
	}
	c, ok := strategyutil.BuildScalpContext(strategyutil.ScalpContextConfig{
		PipSize: cfg.PipSize, ContextMaxAgeSeconds: cfg.ContextMaxAgeSeconds, ContextATRBars: cfg.ContextATRBars,
		ActiveRangeBars: cfg.ActiveRangeBars, ConfirmationWindowBars: cfg.ConfirmationWindowBars,
	}, m5, m1All, now)
	if !ok {
		return nil
	}
	low, high := c.ActiveLow, c.ActiveHigh
	if (high-low)/cfg.PipSize < cfg.MinimumRangeWidthPips {
		return nil
	}

	// Dealing-range position of the context price, from the M15 window (the M5
	// window when no M15 is available), as the context snapshot carried it.
	source := m5
	if m15tf := ctx.Timeframes[market.M15]; m15tf != nil {
		if m15 := closedBy(m15tf.Candles, c.CloseAt, 15, cfg.DealingWindowBars); len(m15) > 0 {
			source = m15
		}
	}
	position, hasPosition := strategyutil.ScalpDealingPosition(source, c.Price, fib.Config{})

	buffer := strategyutil.ScalpBuffer(c.M1ATR, cfg.BufferM1ATRMultiple, cfg.PipSize, cfg.MaximumSpreadPips, cfg.BufferMinSpreadMultiple)
	var out []opportunity.Candidate
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		edge := low
		if direction == market.Sell {
			edge = high
		}
		reclaim, found := detectSweepReclaim(m1, direction, edge, buffer, cfg.TriggerMaximumAgeBars)
		if !found {
			continue
		}
		if hasPosition && (direction == market.Buy && position > cfg.BuyMaximumPosition || direction == market.Sell && position < cfg.SellMinimumPosition) {
			continue
		}
		zoneLow, zoneHigh := low-buffer, low+2*buffer
		worst := zoneHigh
		stopPrice := reclaim.Extreme - buffer
		structural := (worst - stopPrice) / cfg.PipSize
		room := c.BuyRoomPips
		if direction == market.Sell {
			zoneLow, zoneHigh = high-2*buffer, high+buffer
			worst = zoneLow
			stopPrice = reclaim.Extreme + buffer
			structural = (stopPrice - worst) / cfg.PipSize
			room = c.SellRoomPips
		}
		stop, ok := strategyutil.ScalpStopPips(structural, cfg.StopMinimumPips, cfg.StopMaximumPips)
		if !ok {
			continue
		}
		invalidation := worst - stop*cfg.PipSize
		if direction == market.Sell {
			invalidation = worst + stop*cfg.PipSize
		}
		if direction == market.Buy && !(invalidation < zoneLow && zoneLow < zoneHigh) || direction == market.Sell && !(invalidation > zoneHigh && zoneHigh > zoneLow) {
			continue
		}
		targetPrice, targetPips, ok := strategyutil.ScalpTarget(direction, worst, room, stop, cfg.MinimumNetTargetPips, cfg.PipSize, cfg.RewardRiskLadder)
		if !ok {
			continue
		}
		depth := strategyutil.Clamp01(math.Abs(reclaim.Close-edge) / c.ATR)
		label := "1to1"
		if math.Abs(targetPips-2*stop) < 1e-9 {
			label = "1to2"
		}
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("rangesweep:%s:%.*f", direction, 2, edge), Symbol: ctx.Symbol, Direction: direction,
			EntryLow: zoneLow, EntryHigh: zoneHigh, Invalidation: invalidation, InvalidationLabel: "m1_sweep_extreme",
			Target: targetPrice, TargetLabel: "scalp_" + label, Evidence: []string{"m5_range_context", "m1_edge_sweep", "m1_reclaim"},
			Quality:  opportunity.StrategyQuality{Overall: depth, Components: map[string]float64{"sweep_depth": depth, "reclaim": 1}},
			FormedAt: minInt64(m5[0].Time, reclaim.Time), ConfirmedAt: reclaim.Time, ExpiryHours: cfg.ExpiryMinutes / 60, Fingerprint: s.fingerprint,
		})
		if err != nil {
			continue
		}
		out = append(out, candidate)
	}
	return out
}

// sweepReclaim is a closed M1 bar that touched or pierced the range edge and
// closed back inside it in the trade direction.
type sweepReclaim struct {
	Time    int64
	Extreme float64
	Close   float64
}

// detectSweepReclaim mirrors microstructure.detect_sweep_reclaim: the newest of
// the last `lookback` closed bars that reaches the edge within `tolerance` of it
// (touch or pierce), closes back at or inside it and closes in the trade
// direction.
func detectSweepReclaim(bars []market.Candle, direction market.Direction, edge, tolerance float64, lookback int) (sweepReclaim, bool) {
	if len(bars) == 0 {
		return sweepReclaim{}, false
	}
	tol := math.Max(0, tolerance)
	window := lookback
	if window < 1 {
		window = 1
	}
	if window > len(bars) {
		window = len(bars)
	}
	for offset := 1; offset <= window; offset++ {
		bar := bars[len(bars)-offset]
		if direction == market.Buy {
			if bar.Low > edge+tol || bar.Close < edge || bar.Close <= bar.Open {
				continue
			}
			return sweepReclaim{Time: bar.Time, Extreme: bar.Low, Close: bar.Close}, true
		}
		if bar.High < edge-tol || bar.Close > edge || bar.Close >= bar.Open {
			continue
		}
		return sweepReclaim{Time: bar.Time, Extreme: bar.High, Close: bar.Close}, true
	}
	return sweepReclaim{}, false
}

func minInt64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}
