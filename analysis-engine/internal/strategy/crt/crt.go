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
)

const ID strategy.StrategyID = "crt"
const Version = "v2"

type Strategy struct {
	impulseATR, invalidationATR, expiryHours float64
	windowBars                               int
	entryMaxWidthPrice, pipSize              float64
	reaction                                 strategyutil.ReactionConfig
	legacy                                   strategyutil.LegacyDetectorSettings
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
	if s.legacy, err = strategyutil.ParseLegacyDetectorSettings(c.Parameters); err != nil {
		return nil, err
	}
	if s.impulseATR <= 0 || s.invalidationATR <= 0 || s.expiryHours <= 0 || s.windowBars < 50 || s.entryMaxWidthPrice <= 0 || s.pipSize <= 0 {
		return nil, fmt.Errorf("crt: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Evaluate emits the confirmed reaction the frozen CRT publisher
// (technique_detectors.crt_technique_reaction) decides: an H1 impulse range the
// execution timeframe swept and reclaimed, qualified through the shared
// detector contract.
func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	return strategyutil.ConfirmedTechnique(ctx, s.legacy, strategyutil.TechniqueCRT, "", strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, ZoneEvidence: "h1_impulse_range", ConfirmedEvidence: "m5_range_sweep_reclaim",
		InvalidationLabel: "crt_reclaim_failed", InvalidationBufferATR: s.invalidationATR, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		Target: func(dec *strategyutil.TechniqueDecision) (float64, string) {
			if dec.Direction == market.Sell {
				return dec.Instance.StructuralLow, "opposite_h1_range"
			}
			return dec.Instance.StructuralHigh, "opposite_h1_range"
		},
	})
}
