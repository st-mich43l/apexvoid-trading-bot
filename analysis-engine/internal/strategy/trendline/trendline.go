// Package trendline implements causal trendline reaction and reclaimed-break
// setups over internal/trendline's immutable anchors and interaction state.
package trendline

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	technical "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

const ID strategy.StrategyID = "trendline"
const Version = "v2"

type Strategy struct {
	minimumTouches                        int
	invalidationATR, targetR, expiryHours float64
	fingerprint                           string
	// Legacy Python trendline-V2 gates (config trendlines.*).
	interactionBandATR, closeViolationATR, approachMinDistanceATR float64
	maximumBarsSinceLastTouch                                     int
	reaction                                                      strategyutil.ReactionConfig
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("trendline: wrong strategy ID %q", cfg.ID)
	}
	touches, err := strategyutil.Int(cfg.Parameters, "minimum_validation_touches")
	if err != nil {
		return nil, err
	}
	invalid, err := strategyutil.Float(cfg.Parameters, "invalidation_buffer_atr")
	if err != nil {
		return nil, err
	}
	targetR, err := strategyutil.Float(cfg.Parameters, "target_r")
	if err != nil {
		return nil, err
	}
	expiry, err := strategyutil.Float(cfg.Parameters, "expiry_hours")
	if err != nil {
		return nil, err
	}
	band, err := strategyutil.Float(cfg.Parameters, "interaction_band_atr")
	if err != nil {
		return nil, err
	}
	closeViolation, err := strategyutil.Float(cfg.Parameters, "close_violation_atr")
	if err != nil {
		return nil, err
	}
	approach, err := strategyutil.Float(cfg.Parameters, "approach_min_distance_atr")
	if err != nil {
		return nil, err
	}
	maxBars, err := strategyutil.Int(cfg.Parameters, "maximum_bars_since_last_touch")
	if err != nil {
		return nil, err
	}
	reaction, err := strategyutil.ParseReactionConfig(cfg.Parameters)
	if err != nil {
		return nil, err
	}
	if touches < 1 || invalid <= 0 || targetR <= 0 || expiry <= 0 || band <= 0 || closeViolation <= 0 || approach < 0 || maxBars < 1 {
		return nil, fmt.Errorf("trendline: invalid parameters")
	}
	return &Strategy{
		minimumTouches: touches, invalidationATR: invalid, targetR: targetR, expiryHours: expiry,
		fingerprint:        strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters),
		interactionBandATR: band, closeViolationATR: closeViolation, approachMinDistanceATR: approach,
		maximumBarsSinceLastTouch: maxBars, reaction: reaction,
	}, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	bar, ok := strategyutil.LastBar(ctx, market.M5)
	atr := ctx.Volatility.ATR
	if tf == nil || !ok || atr <= 0 {
		return nil
	}
	var out []opportunity.Candidate
	for _, line := range tf.Trendline.Lines {
		if !s.healthy(line, len(tf.Candles)) {
			continue
		}
		interaction := technical.EvaluateInteraction(tf.Candles, line, atr, technical.Config{InteractionBandATR: s.interactionBandATR, CloseViolationATR: s.closeViolationATR, ApproachMinDistanceATR: s.approachMinDistanceATR})
		direction := market.Buy
		valid := interaction.State == technical.InteractionReclaimedSupport
		if line.Kind == technical.KindResistance {
			direction, valid = market.Sell, interaction.State == technical.InteractionReclaimedResistance
		}
		if !valid {
			continue
		}
		entry := float64(interaction.LinePrice)
		invalid := entry - s.invalidationATR*atr
		if direction == market.Sell {
			invalid = entry + s.invalidationATR*atr
		}
		risk := math.Abs(entry - invalid)
		target := entry + s.targetR*risk
		if direction == market.Sell {
			target = entry - s.targetR*risk
		}
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: fmt.Sprintf("trendline:%s:%s", line.AnchorA, line.AnchorB), Symbol: ctx.Symbol, Direction: direction,
			EntryLow: float64(interaction.BandLow), EntryHigh: float64(interaction.BandHigh), Invalidation: invalid, InvalidationLabel: "trendline_close_violation",
			Target: target, TargetLabel: "trendline_projection", Evidence: []string{"m5_trendline_causal_anchors", "m5_trendline_reclaimed"},
			Quality:  opportunity.StrategyQuality{Overall: strategyutil.Clamp01(float64(len(line.ValidationTouches)) / 4), Components: map[string]float64{"validation_touches": strategyutil.Clamp01(float64(len(line.ValidationTouches)) / 4), "rejection": 1}},
			FormedAt: bar.Time - int64(line.SpanBars)*300, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		})
		if err == nil {
			out = append(out, candidate)
			lineID := fmt.Sprintf("trendline:%s:%s", line.AnchorA, line.AnchorB)
			if rc := strategyutil.ConfirmReaction(tf, lineID, direction, float64(interaction.BandLow), float64(interaction.BandHigh), atr, 0, s.reaction); rc != nil {
				if confirmed, confirmErr := strategyutil.ConfirmedVariant(candidate, rc, lineID, candidate.FormedAt, s.expiryHours, "m5_trendline_reaction_confirmed"); confirmErr == nil {
					out = append(out, confirmed)
				}
			}
		}
	}
	return out
}

// healthy is the legacy Python trendline-V2 structural gate: a line must have
// forward validation, must not be tentative, broken, degraded or exhausted,
// and must have been touched recently enough to still matter.
func (s *Strategy) healthy(line technical.Trendline, candleCount int) bool {
	switch line.State {
	case technical.StateTentative, technical.StateBroken, technical.StateDegraded, technical.StateExhausted:
		return false
	}
	if line.Exhausted || line.BrokenAt != nil || len(line.ValidationTouches) < s.minimumTouches {
		return false
	}
	lastTouch := line.ValidationTouches[len(line.ValidationTouches)-1].BarIndex
	return candleCount-1-lastTouch <= s.maximumBarsSinceLastTouch
}
