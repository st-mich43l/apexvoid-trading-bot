// Package crt implements the frozen-Python-parity H1 candle-range sweep and
// M5 reclaim thesis inside the causal Go strategy runtime.
package crt

import (
	"fmt"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "crt"
const Version = "v2"

type Strategy struct {
	impulseATR, invalidationATR, expiryHours float64
	windowBars                               int
	entryMaxWidthPrice, pipSize              float64
	reaction                                 strategyutil.ReactionConfig
	fingerprint                              string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("crt: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	for i, key := range []string{"minimum_h1_range_atr", "invalidation_buffer_atr", "expiry_hours"} {
		value, err := strategyutil.Float(c.Parameters, key)
		if err != nil {
			return nil, err
		}
		*[]*float64{&s.impulseATR, &s.invalidationATR, &s.expiryHours}[i] = value
	}
	var err error
	if s.windowBars, err = strategyutil.Int(c.Parameters, "technique_window_bars"); err != nil {
		return nil, err
	}
	if s.entryMaxWidthPrice, err = strategyutil.Float(c.Parameters, "entry_max_width_price"); err != nil {
		return nil, err
	}
	if s.pipSize, err = strategyutil.Float(c.Parameters, "pip_size"); err != nil {
		return nil, err
	}
	if s.reaction, err = strategyutil.ParseReactionConfig(c.Parameters); err != nil {
		return nil, err
	}
	if s.impulseATR <= 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 || s.windowBars < 50 || s.entryMaxWidthPrice <= 0 || s.pipSize <= 0 {
		return nil, fmt.Errorf("crt: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	h1, execTF := ctx.Timeframes[market.H1], ctx.Timeframes[market.M5]
	if h1 == nil || execTF == nil || len(h1.Candles) < 20 || len(execTF.Candles) < 20 {
		return nil
	}
	exec := execTF.Candles
	if len(exec) > s.windowBars {
		exec = exec[len(exec)-s.windowBars:]
	}
	h1ATR := techniquezone.ATRScalar(techniquezone.ATRSeries(h1.Candles, 14), 1)
	execATR := techniquezone.ATRScalar(techniquezone.ATRSeries(exec, 14), 1)
	crtSettings := techniquezone.ProductionCRTSettings()
	crtSettings.MinATR = s.impulseATR
	crtSettings.EntryMaxWidthPrice = s.entryMaxWidthPrice
	techniqueSettings := techniquezone.ProductionTechniqueSettings()
	techniqueSettings.PipSize = s.pipSize
	var out []opportunity.Candidate
	for _, instance := range techniquezone.CollectCRT(h1.Candles, exec, h1ATR, execATR, crtSettings, techniqueSettings) {
		direction := market.Buy
		invalidation := instance.StructuralLow - s.invalidationATR*execATR
		target := instance.StructuralHigh
		if instance.Side == "sell" {
			direction = market.Sell
			invalidation = instance.StructuralHigh + s.invalidationATR*execATR
			target = instance.StructuralLow
		}
		setupKey := fmt.Sprintf("crt:%d", instance.H1Time)
		reaction := strategyutil.ConfirmReaction(execTF, setupKey, direction, instance.StructuralLow, instance.StructuralHigh, execATR, instance.H1Time, s.reaction)
		if reaction == nil {
			continue
		}
		quality := strategyutil.Clamp01((instance.StructuralHigh-instance.StructuralLow)/(s.impulseATR*h1ATR) - .25)
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: instance.Low, EntryHigh: instance.High, Invalidation: invalidation, InvalidationLabel: "crt_reclaim_failed",
			Target: target, TargetLabel: "opposite_h1_range", Evidence: []string{"h1_impulse_range", "m5_range_sweep_reclaim", "python_parity_crt_geometry"},
			Quality:  opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{"h1_impulse": quality, "m5_reclaim": 1}},
			FormedAt: instance.H1Time, ConfirmedAt: reaction.ConfirmationBarTime, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		})
		if err != nil {
			continue
		}
		// CollectCRT already proves a reaction. Re-evaluating through the shared
		// contract attaches its exact touch/confirmation provenance.
		candidate.Reaction = reaction
		out = append(out, candidate)
	}
	return out
}
