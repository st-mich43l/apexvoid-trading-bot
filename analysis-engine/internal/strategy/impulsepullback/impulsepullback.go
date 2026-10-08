// Package impulsepullback is the Go port of the Python scalp lane's Impulse
// Pullback Scalp (app/scalping discover_impulse_pullback, as it ran in the
// profitable XAU week): an M5 impulse leg qualified by displacement and body
// dominance, a corrective pullback of the right depth, a structural reference
// (an unmitigated M5 supply/demand zone, else a nearby key level) whose role
// agrees with the trade, an M1 confirmation at that reference, and a structural
// stop and corridor target. It is evaluated on every closed M1 bar.
package impulsepullback

import (
	"fmt"
	"math"
	"strings"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "impulse_pullback"
const Version = "v3"

// Config is the strategy's parameters: the lane's window and context shape, the
// setup-window structure, the archetype's qualification and the shared stop and
// target book. Every value is the profitable week's (auto_algo.strategies.scalping.*).
type Config struct {
	Symbols                                                                                 []string
	PipSize                                                                                 float64
	SetupWindowBars, DealingWindowBars, ConfirmationWindowBars, ConfirmationLookbackBars    int
	ContextMaxAgeSeconds                                                                    int64
	ContextATRBars, ActiveRangeBars, PullbackExtremeConfirmBars, BreakoutAcceptBars         int
	ExpiryMinutes                                                                           float64
	BuyMaximumPosition, SellMinimumPosition                                                 float64
	LevelProximityATR, ZoneMaximumATR                                                       float64
	ImpulseDisplacementATR, ImpulseBodyDominance, PullbackCorrectiveRatio                   float64
	BufferM1ATRMultiple, BufferMinSpreadMultiple, MaximumSpreadPips                         float64
	StopMinimumPips, StopMaximumPips, MinimumNetTargetPips                                  float64
	SwingFractalN, LevelMinimumTouches                                                      int
	LevelClusterATR, LevelRoundStep, LevelMaxClusterSpan, DisplacementATR, DisplacementBody float64
	RewardRiskLadder                                                                        []float64
}

type Strategy struct {
	cfg         Config
	fingerprint string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("impulsepullback: wrong ID")
	}
	cfg, err := parseConfig(c.Parameters)
	if err != nil {
		return nil, fmt.Errorf("impulsepullback: %w", err)
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
	cfg.SetupWindowBars, cfg.DealingWindowBars = count("setup_window_bars"), count("dealing_window_bars")
	cfg.ConfirmationWindowBars, cfg.ConfirmationLookbackBars = count("confirmation_window_bars"), count("confirmation_lookback_bars")
	cfg.ContextMaxAgeSeconds = int64(count("context_max_age_seconds"))
	cfg.ContextATRBars, cfg.ActiveRangeBars = count("context_atr_bars"), count("active_range_bars")
	cfg.PullbackExtremeConfirmBars, cfg.BreakoutAcceptBars = count("pullback_extreme_confirm_bars"), count("breakout_accept_bars")
	cfg.ExpiryMinutes = num("expiry_minutes")
	cfg.BuyMaximumPosition, cfg.SellMinimumPosition = num("buy_maximum_position"), num("sell_minimum_position")
	cfg.LevelProximityATR, cfg.ZoneMaximumATR = num("level_proximity_atr_multiple"), num("zone_maximum_atr_multiple")
	cfg.ImpulseDisplacementATR, cfg.ImpulseBodyDominance = num("impulse_displacement_atr_multiple"), num("impulse_body_dominance")
	cfg.PullbackCorrectiveRatio = num("pullback_corrective_ratio")
	cfg.BufferM1ATRMultiple, cfg.BufferMinSpreadMultiple = num("buffer_m1_atr_multiple"), num("buffer_minimum_spread_multiple")
	cfg.MaximumSpreadPips = num("maximum_spread_pips")
	cfg.StopMinimumPips, cfg.StopMaximumPips = num("stop_minimum_pips"), num("stop_maximum_pips")
	cfg.MinimumNetTargetPips = num("minimum_net_target_pips")
	cfg.SwingFractalN, cfg.LevelMinimumTouches = count("swing_fractal_n"), count("level_minimum_touches")
	cfg.LevelClusterATR, cfg.LevelRoundStep, cfg.LevelMaxClusterSpan = num("level_cluster_atr"), num("level_round_step"), num("level_max_cluster_span")
	cfg.DisplacementATR, cfg.DisplacementBody = num("displacement_atr_multiple"), num("displacement_body_fraction")
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
	case cfg.PipSize <= 0, cfg.SetupWindowBars < 10, cfg.ConfirmationWindowBars < 5, cfg.ConfirmationLookbackBars < 1, cfg.ContextATRBars < 1,
		cfg.ActiveRangeBars < 2, cfg.ExpiryMinutes <= 0, cfg.PullbackExtremeConfirmBars < 1, cfg.BreakoutAcceptBars < 1,
		cfg.StopMinimumPips <= 0, cfg.StopMaximumPips < cfg.StopMinimumPips,
		cfg.ImpulseDisplacementATR <= 0, cfg.ImpulseBodyDominance < 0, cfg.PullbackCorrectiveRatio <= 0, cfg.ZoneMaximumATR <= 0:
		return Config{}, fmt.Errorf("invalid parameters")
	}
	return cfg, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

// RequiredTimeframes is M1 only: the Python lane ran once per closed M1 bar.
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M1} }

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

// reference is the structural object an impulse pullback is anchored to.
type reference struct {
	Zone               bool
	Bottom, Top, Level float64
	Band               float64
	RawKind            string
	Distance           float64
	Score              float64
	Touches            int
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
	source := m5
	if m15tf := ctx.Timeframes[market.M15]; m15tf != nil {
		if m15 := closedBy(m15tf.Candles, c.CloseAt, 15, cfg.DealingWindowBars); len(m15) > 0 {
			source = m15
		}
	}
	position, hasPosition := strategyutil.ScalpDealingPosition(source, c.Price, fib.Config{})

	levels, zones := scalpStructure(cfg, m5)
	buffer := strategyutil.ScalpBuffer(c.M1ATR, cfg.BufferM1ATRMultiple, cfg.PipSize, cfg.MaximumSpreadPips, cfg.BufferMinSpreadMultiple)
	closes := make([]float64, len(m5))
	for i, bar := range m5 {
		closes[i] = bar.Close
	}
	var out []opportunity.Candidate
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		if hasPosition && (direction == market.Buy && position > cfg.BuyMaximumPosition || direction == market.Sell && position < cfg.SellMinimumPosition) {
			continue
		}
		ev, found := detectImpulsePullback(m5, direction, cfg.PullbackExtremeConfirmBars)
		if !found || ev.Rejected {
			continue
		}
		m1ATR := math.Max(c.M1ATR, cfg.PipSize*3)
		structureATR := math.Max(c.ATR, cfg.PipSize*3)
		displacement := ev.ImpulseLen / structureATR
		if displacement < cfg.ImpulseDisplacementATR || ev.BodyDominance < cfg.ImpulseBodyDominance {
			continue
		}
		if ev.MeanImpulseBody <= 0 || ev.MeanPullbackBody >= ev.MeanImpulseBody*cfg.PullbackCorrectiveRatio {
			continue
		}
		ref, rejected, hasRef := s.impulseReference(zones, levels, direction, ev.PullbackExtreme, m1ATR)
		if !hasRef || rejected {
			continue
		}
		confirm, confirmed := strategyutil.ConfirmM1Execution(m1, direction, ref.Level, buffer, cfg.ConfirmationLookbackBars)
		if !confirmed {
			continue
		}
		if s.levelRole(ref, direction, closes) != expectedRole(direction) {
			continue
		}
		zoneLow, zoneHigh := ref.Bottom, ref.Top
		if !ref.Zone {
			if direction == market.Buy {
				zoneLow, zoneHigh = ref.Level, ref.Level+buffer
			} else {
				zoneLow, zoneHigh = ref.Level-buffer, ref.Level
			}
		}
		worst := zoneHigh
		stopPrice := ref.Level - buffer
		structural := (worst - stopPrice) / cfg.PipSize
		room := c.BuyRoomPips
		if direction == market.Sell {
			worst = zoneLow
			stopPrice = ref.Level + buffer
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
		if !ok || targetPips > room*0.9 {
			continue
		}
		quality := strategyutil.Clamp01(displacement / (2 * cfg.ImpulseDisplacementATR))
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("impulse:%s:%.2f:%.2f:%.2f", direction, ev.Origin, ev.Extreme, ref.Level), Symbol: ctx.Symbol, Direction: direction,
			EntryLow: zoneLow, EntryHigh: zoneHigh, Invalidation: invalidation, InvalidationLabel: "impulse_reference_lost",
			Target: targetPrice, TargetLabel: "scalp_corridor", Evidence: []string{"m5_qualified_impulse", "m1_bounded_pullback", "m1_continuation_trigger"},
			Quality:  opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{"displacement": quality, "body_dominance": strategyutil.Clamp01(ev.BodyDominance)}},
			FormedAt: ev.BarTime, ConfirmedAt: confirm.Time, ExpiryHours: cfg.ExpiryMinutes / 60, Fingerprint: s.fingerprint,
		})
		if err != nil {
			continue
		}
		out = append(out, candidate)
	}
	return out
}

func expectedRole(direction market.Direction) keylevel.RoleKind {
	if direction == market.Buy {
		return keylevel.RoleSupport
	}
	return keylevel.RoleResistance
}

// scalpStructure is scalp_structure(): the setup window's key levels and
// mitigation-stamped supply/demand zones (no trendline or technique stack).
func scalpStructure(cfg Config, m5 []market.Candle) ([]techniquezone.Level, []techniquezone.Zone) {
	atr := techniquezone.ATRSeries(m5, cfg.ContextATRBars)
	swings := techniquezone.FindSwings(m5, cfg.SwingFractalN, 0, 1, atr, -1)
	levels := techniquezone.KeyLevels(swings, atr, cfg.LevelClusterATR, cfg.LevelRoundStep, cfg.LevelMinimumTouches, cfg.LevelMaxClusterSpan, nil)
	legs := techniquezone.Displacement(m5, atr, cfg.DisplacementATR, cfg.DisplacementBody)
	zones := techniquezone.MarkMitigation(techniquezone.SupplyDemand(m5, legs), m5, len(m5))
	return levels, zones
}

// impulseReference mirrors _impulse_reference: the best unmitigated same-side M5
// zone near the pullback extreme (highest score, then touches, then nearest),
// rejected when wider than the zone cap; else the nearest key level.
func (s *Strategy) impulseReference(zones []techniquezone.Zone, levels []techniquezone.Level, direction market.Direction, extreme, m1ATR float64) (reference, bool, bool) {
	proximity := math.Max(0, s.cfg.LevelProximityATR*m1ATR)
	side := "supply"
	if direction == market.Buy {
		side = "demand"
	}
	var best *reference
	for _, z := range zones {
		if z.Side != side || z.Mitigated || z.Top <= z.Bottom {
			continue
		}
		if direction == market.Buy && z.Bottom > extreme || direction == market.Sell && z.Top < extreme {
			continue
		}
		distance := 0.0
		if !(z.Bottom <= extreme && extreme <= z.Top) {
			distance = math.Min(math.Abs(extreme-z.Bottom), math.Abs(extreme-z.Top))
		}
		if distance > proximity {
			continue
		}
		level := z.Top
		if direction == market.Buy {
			level = z.Bottom
		}
		candidate := reference{Zone: true, Bottom: z.Bottom, Top: z.Top, Level: level, Distance: distance, Score: z.Score, Touches: z.Touches}
		if best == nil || candidate.Score > best.Score || candidate.Score == best.Score &&
			(candidate.Touches > best.Touches || candidate.Touches == best.Touches && candidate.Distance < best.Distance) {
			c := candidate
			best = &c
		}
	}
	if best != nil {
		if best.Top-best.Bottom > s.cfg.ZoneMaximumATR*m1ATR {
			return reference{}, true, true
		}
		return *best, false, true
	}
	var nearest *reference
	for _, l := range levels {
		band := math.Max(0, l.Band)
		distance := math.Abs(l.Price - extreme)
		if distance > proximity+band {
			continue
		}
		if direction == market.Buy && l.Price > extreme+proximity || direction == market.Sell && l.Price < extreme-proximity {
			continue
		}
		candidate := reference{Level: l.Price, Band: band, RawKind: l.Kind, Bottom: l.Price, Top: l.Price, Distance: distance, Touches: l.Touches}
		if nearest == nil || candidate.Distance < nearest.Distance || candidate.Distance == nearest.Distance && candidate.Touches > nearest.Touches {
			c := candidate
			nearest = &c
		}
	}
	if nearest == nil {
		return reference{}, false, false
	}
	return *nearest, false, true
}

// levelRole mirrors _impulse_level_role: the closed-bar role of the reference.
func (s *Strategy) levelRole(ref reference, direction market.Direction, closes []float64) keylevel.RoleKind {
	kind := "support"
	if direction == market.Sell {
		kind = "resistance"
	}
	low, high := ref.Bottom, ref.Top
	if !ref.Zone {
		if raw := ref.RawKind; explicitRole(raw) {
			kind = raw
		}
		low, high = ref.Level-ref.Band, ref.Level+ref.Band
	}
	return keylevel.Role(kind, market.Price(low), market.Price(high), closes, s.cfg.BreakoutAcceptBars)
}

func explicitRole(kind string) bool {
	lower := strings.ToLower(kind)
	for _, token := range []string{"support", "resistance", "resist", "swing_low", "swing_high"} {
		if strings.Contains(lower, token) {
			return true
		}
	}
	return false
}

// impulseEvent is detect_impulse_pullback's measurement of the latest leg.
type impulseEvent struct {
	Rejected                          bool
	Origin, Extreme, PullbackExtreme  float64
	ImpulseLen, BodyDominance         float64
	MeanImpulseBody, MeanPullbackBody float64
	BarTime                           int64
}

// detectImpulsePullback mirrors microstructure.detect_impulse_pullback over the
// last 30 setup bars: the origin is the window's extreme low (BUY) or high
// (SELL), the extreme the farthest point of the leg after it, and the pullback
// must retrace 25-75% of the leg, with the last bar continuing, the
// continuation not overextended, and a confirmed pullback extreme.
func detectImpulsePullback(bars []market.Candle, direction market.Direction, confirmBars int) (impulseEvent, bool) {
	if len(bars) < 8 {
		return impulseEvent{}, false
	}
	window := bars
	if len(window) > 30 {
		window = window[len(window)-30:]
	}
	n := len(window)
	last := window[n-1]
	current := last.Close
	buy := direction == market.Buy
	// Index of the first extreme of each kind, as numpy argmin/argmax return it.
	originI := 0
	for i := 1; i < n; i++ {
		if buy && window[i].Low < window[originI].Low || !buy && window[i].High > window[originI].High {
			originI = i
		}
	}
	extremeI := originI
	for i := originI + 1; i < n; i++ {
		if buy && window[i].High > window[extremeI].High || !buy && window[i].Low < window[extremeI].Low {
			extremeI = i
		}
	}
	origin, extreme := window[originI].Low, window[extremeI].High
	impulseLen := extreme - origin
	pullback := extreme - current
	if !buy {
		origin, extreme = window[originI].High, window[extremeI].Low
		impulseLen = origin - extreme
		pullback = current - extreme
	}
	if impulseLen <= 0 {
		return impulseEvent{}, false
	}
	retracement := pullback / impulseLen
	if retracement < 0.25 || retracement > 0.75 {
		return impulseEvent{Rejected: true}, true
	}
	if buy && last.Close <= last.Open || !buy && last.Close >= last.Open {
		return impulseEvent{}, false
	}
	if math.Abs(current-extreme)/impulseLen < 0.05 {
		return impulseEvent{Rejected: true}, true
	}
	confirm := confirmBars
	if confirm < 1 {
		confirm = 1
	}
	confirmedEnd := n - confirm
	confirmedLo, confirmedHi := extremeI, confirmedEnd
	if confirmedHi < confirmedLo {
		confirmedHi = confirmedLo
	}
	if confirmedEnd <= extremeI {
		return impulseEvent{Rejected: true}, true
	}
	tailStart := confirmedEnd
	if tailStart < extremeI {
		tailStart = extremeI
	}
	var pullbackExtreme float64
	if buy {
		pullbackExtreme = window[confirmedLo].Low
		for i := confirmedLo; i < confirmedHi; i++ {
			pullbackExtreme = math.Min(pullbackExtreme, window[i].Low)
		}
		for i := tailStart; i < n; i++ {
			if window[i].Low <= pullbackExtreme {
				return impulseEvent{Rejected: true}, true
			}
		}
	} else {
		pullbackExtreme = window[confirmedLo].High
		for i := confirmedLo; i < confirmedHi; i++ {
			pullbackExtreme = math.Max(pullbackExtreme, window[i].High)
		}
		for i := tailStart; i < n; i++ {
			if window[i].High >= pullbackExtreme {
				return impulseEvent{Rejected: true}, true
			}
		}
	}
	var impulseBody, impulseRange, pullbackBody float64
	for i := originI; i <= extremeI; i++ {
		impulseBody += math.Abs(window[i].Close - window[i].Open)
		impulseRange += math.Abs(window[i].High - window[i].Low)
	}
	impulseBars := float64(extremeI - originI + 1)
	var pullbackBars float64
	for i := extremeI + 1; i < n; i++ {
		pullbackBody += math.Abs(window[i].Close - window[i].Open)
		pullbackBars++
	}
	meanImpulseRange, meanImpulseBody := impulseRange/impulseBars, impulseBody/impulseBars
	meanPullbackBody := 0.0
	if pullbackBars > 0 {
		meanPullbackBody = pullbackBody / pullbackBars
	}
	dominance := 0.0
	if meanImpulseRange > 0 {
		dominance = meanImpulseBody / meanImpulseRange
	}
	return impulseEvent{
		Origin: origin, Extreme: extreme, PullbackExtreme: pullbackExtreme, ImpulseLen: impulseLen, BodyDominance: dominance,
		MeanImpulseBody: meanImpulseBody, MeanPullbackBody: meanPullbackBody, BarTime: last.Time,
	}, true
}
