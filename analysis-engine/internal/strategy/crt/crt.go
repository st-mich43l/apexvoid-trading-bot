// Package crt implements the approved H1 candle-range sweep and M5 reclaim
// thesis. The H1 impulse candle owns the range; M5 confirms the trade.
package crt

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/legacyzone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "crt"
const Version = "v2"

type Strategy struct {
	impulseATR, invalidationATR, expiryHours float64
	fingerprint                              string
	// legacy switches discovery to the Python technique (override-only
	// parameters, set per symbol by the engine with the Python-parity zones).
	legacy *legacyConfig
}

type legacyConfig struct {
	windowBars         int
	entryMaxWidthPrice float64
	pipSize            float64
	reaction           strategyutil.ReactionConfig
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("crt: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	for i, k := range []string{"minimum_h1_range_atr", "invalidation_buffer_atr", "expiry_hours"} {
		v, e := strategyutil.Float(c.Parameters, k)
		if e != nil {
			return nil, e
		}
		*[]*float64{&s.impulseATR, &s.invalidationATR, &s.expiryHours}[i] = v
	}
	if s.impulseATR <= 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("crt: invalid parameters")
	}
	if raw, ok := c.Parameters["legacy_window_bars"]; ok {
		window, e := strategyutil.Int(map[string]any{"legacy_window_bars": raw}, "legacy_window_bars")
		if e != nil {
			return nil, e
		}
		entryMax, e := strategyutil.Float(c.Parameters, "entry_max_width_price")
		if e != nil {
			return nil, e
		}
		pip, e := strategyutil.Float(c.Parameters, "pip_size")
		if e != nil {
			return nil, e
		}
		reaction, e := strategyutil.ParseReactionConfig(c.Parameters)
		if e != nil {
			return nil, e
		}
		s.legacy = &legacyConfig{windowBars: window, entryMaxWidthPrice: entryMax, pipSize: pip, reaction: reaction}
	}
	return s, nil
}
func (s *Strategy) ID() strategy.StrategyID { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	if s.legacy != nil {
		// The Python CRT ran on the execution timeframe's bars with the H1
		// frame as context: evaluating it on an H1 close as well would
		// observe the same opportunity at the H1 bar's (older) open time.
		return []market.Timeframe{market.M5}
	}
	return []market.Timeframe{market.H1, market.M5}
}
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	if s.legacy != nil {
		return s.evaluateLegacy(ctx)
	}
	h := ctx.Timeframes[market.H1]
	m := ctx.Timeframes[market.M5]
	atr := ctx.Volatility.ATR
	if h == nil || m == nil || atr <= 0 || len(h.Candles) < 2 || len(m.Candles) < 2 {
		return nil
	}
	anchor := h.Candles[len(h.Candles)-2]
	if anchor.Range() < s.impulseATR*atr {
		return nil
	}
	bar := m.Candles[len(m.Candles)-1]
	direction := market.Direction("")
	if bar.Low < anchor.Low && bar.Close > anchor.Low {
		direction = market.Buy
	} else if bar.High > anchor.High && bar.Close < anchor.High {
		direction = market.Sell
	}
	if !direction.IsValid() {
		return nil
	}
	anchorEdge := anchor.Low
	entry := bar.Close
	invalid := math.Min(bar.Low, anchor.Low) - s.invalidationATR*atr
	target := anchor.High
	if direction == market.Sell {
		anchorEdge = anchor.High
		invalid = math.Max(bar.High, anchor.High) + s.invalidationATR*atr
		target = anchor.Low
	}
	// The objective is the opposite edge of the H1 range. If the reclaim bar
	// already closed at or beyond it there is no reward left to publish.
	if direction == market.Buy && float64(target) <= math.Max(entry, float64(anchorEdge)) {
		return nil
	}
	if direction == market.Sell && float64(target) >= math.Min(entry, float64(anchorEdge)) {
		return nil
	}
	q := strategyutil.Clamp01(anchor.Range()/(s.impulseATR*atr) - 0.25)
	c, e := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("crt:%d", anchor.Time), Symbol: ctx.Symbol, Direction: direction, EntryLow: math.Min(entry, float64(anchorEdge)), EntryHigh: math.Max(entry, float64(anchorEdge)), Invalidation: invalid, InvalidationLabel: "crt_reclaim_failed", Target: target, TargetLabel: "opposite_h1_range", Evidence: []string{"h1_impulse_range", "m5_range_sweep_reclaim"}, Quality: opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"h1_impulse": q, "m5_reclaim": 1}}, FormedAt: anchor.Time, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if e != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}

// evaluateLegacy is the Python CRT technique: a recent closed H1 candle (last
// three, at least impulseATR H1-ATRs tall) that M5 swept and reclaimed within
// six bars, price in the correct half of the range, a structural reaction off
// the full H1 range, validated like every technique instance. The entry is the
// swept-edge slice (capped at the instrument's entry width); the stop sits
// beyond the full H1 range; the target is its opposite edge.
func (s *Strategy) evaluateLegacy(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	h, m := ctx.Timeframes[market.H1], ctx.Timeframes[market.M5]
	if h == nil || m == nil || len(h.Candles) < 20 || len(m.Candles) < 20 {
		return nil
	}
	exec := m.Candles
	if len(exec) > s.legacy.windowBars {
		exec = exec[len(exec)-s.legacy.windowBars:]
	}
	h1ATR := legacyzone.ATRScalar(legacyzone.ATRSeries(h.Candles, 14), 1)
	execATR := legacyzone.ATRScalar(legacyzone.ATRSeries(exec, 14), 1)
	crtSettings := legacyzone.ProductionCRTSettings()
	crtSettings.MinATR = s.impulseATR
	crtSettings.EntryMaxWidthPrice = s.legacy.entryMaxWidthPrice
	techSettings := legacyzone.ProductionTechniqueSettings()
	techSettings.PipSize = s.legacy.pipSize
	var out []opportunity.Candidate
	for _, in := range legacyzone.CollectCRT(h.Candles, exec, h1ATR, execATR, crtSettings, techSettings) {
		direction := market.Buy
		invalidation := in.StructuralLow - s.invalidationATR*execATR
		target := in.StructuralHigh
		if in.Side == "sell" {
			direction = market.Sell
			invalidation = in.StructuralHigh + s.invalidationATR*execATR
			target = in.StructuralLow
		}
		setupKey := fmt.Sprintf("crt:%d", in.H1Time)
		bar := m.Candles[len(m.Candles)-1]
		q := strategyutil.Clamp01((in.StructuralHigh-in.StructuralLow)/(s.impulseATR*h1ATR) - 0.25)
		c, e := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: in.Low, EntryHigh: in.High, Invalidation: invalidation, InvalidationLabel: "crt_reclaim_failed",
			Target: target, TargetLabel: "opposite_h1_range",
			Evidence: []string{"h1_impulse_range", "m5_range_sweep_reclaim"},
			Quality:  opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"h1_impulse": q, "m5_reclaim": 1}},
			FormedAt: in.H1Time, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		})
		if e != nil {
			continue
		}
		out = append(out, c)
		// Every legacy CRT instance already required a structural reaction off
		// the full H1 range; express it as a confirmed reaction too.
		if rc := strategyutil.ConfirmReaction(m, setupKey, direction, in.StructuralLow, in.StructuralHigh, execATR, in.H1Time, s.legacy.reaction); rc != nil {
			if confirmed, ce := strategyutil.ConfirmedVariant(c, rc, setupKey, in.H1Time, s.expiryHours, "m5_crt_reaction_confirmed"); ce == nil {
				out = append(out, confirmed)
			}
		}
	}
	return out
}
