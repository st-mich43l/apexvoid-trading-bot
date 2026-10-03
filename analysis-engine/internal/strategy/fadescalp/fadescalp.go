// Package fadescalp implements the independent equal-level sweep reversal
// thesis formerly named fade_scalp in Python.
package fadescalp

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "fade_scalp"
const Version = "v2"

type Strategy struct {
	proximalBandATR, invalidationATR, targetR, expiryHours float64
	chopEdgeFraction                                       float64
	reaction                                               strategyutil.ReactionConfig
	strictPD                                               bool
	fingerprint                                            string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("fadescalp: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*float64{"proximal_band_atr": &s.proximalBandATR, "invalidation_buffer_atr": &s.invalidationATR, "target_r": &s.targetR, "expiry_hours": &s.expiryHours, "chop_edge_fraction": &s.chopEdgeFraction} {
		v, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	var err error
	var ok bool
	if s.strictPD, ok = cfg.Parameters["strict_premium_discount"].(bool); !ok {
		return nil, fmt.Errorf("fadescalp: strict_premium_discount must be boolean")
	}
	if s.reaction, err = strategyutil.ParseReactionConfig(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.proximalBandATR <= 0 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 || s.chopEdgeFraction < 0 || s.chopEdgeFraction > .5 {
		return nil, fmt.Errorf("fadescalp: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	bar, ok := strategyutil.LastBar(ctx, market.M5)
	atr := ctx.Volatility.ATR
	direction := strategyutil.StructuralDirection(ctx)
	if tf == nil || !ok || atr <= 0 || !direction.IsValid() || !strategyutil.PremiumDiscountAllows(tf, direction, s.strictPD) {
		return nil
	}
	wantSource, wantSide := "equal_low", liquidity.LiquiditySellSide
	if direction == market.Sell {
		wantSource, wantSide = "equal_high", liquidity.LiquidityBuySide
	}

	var best *opportunity.Candidate
	bestDistance := math.Inf(1)
	for _, pool := range tf.Liquidity.Pools {
		if pool.Source != wantSource || pool.Side != wantSide || pool.SweptAt == nil || pool.ReclaimedAt == nil {
			continue
		}
		half := math.Max(math.Abs(float64(pool.High-pool.Low))/2, s.proximalBandATR*atr)
		level := (float64(pool.Low) + float64(pool.High)) / 2
		low, high := level-half, level+half
		grab := strategyutil.GrabForBand(tf, direction, low, high, 0)
		if grab == nil {
			continue
		}
		if tf.Regime.Kind == "chop" && (grab.Grade != "A" || !strategyutil.ChopEdgeAllows(tf, direction, low, high, s.chopEdgeFraction)) {
			continue
		}
		reaction := strategyutil.ConfirmReaction(tf, pool.ID, direction, low, high, atr, pool.CreatedAt, s.reaction)
		if reaction == nil {
			continue
		}
		invalid, reference := low-s.invalidationATR*atr, high
		if direction == market.Sell {
			invalid, reference = high+s.invalidationATR*atr, low
		}
		risk := math.Abs(reference - invalid)
		target := reference + s.targetR*risk
		if direction == market.Sell {
			target = reference - s.targetR*risk
		}
		if liqTarget, found := strategyutil.OpposingLiquidity(tf.Liquidity.Pools, direction, reference, risk); found {
			target = float64(liqTarget)
		}
		quality := strategyutil.Clamp01(.55 + .15*gradeScore(grab.Grade) + .05*math.Min(float64(pool.TouchCount), 3))
		evidence := []string{"equal_level_" + wantSource, "liquidity_grab_grade_" + grab.Grade, "legacy_pd_location", "structural_reaction_" + reaction.Pattern}
		if tf.Regime.Kind == "chop" {
			evidence = append(evidence, "chop_edge_grade_a")
		}
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: "fade:" + pool.ID, Symbol: ctx.Symbol, Direction: direction, EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "equal_level_reclaim_failed", Target: target, TargetLabel: "fade_opposing_liquidity", Evidence: evidence, Quality: opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{"liquidity_grade": .75 + .25*gradeScore(grab.Grade), "reaction": 1, "location": 1}}, FormedAt: *pool.SweptAt, ConfirmedAt: reaction.ConfirmationBarTime, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
		if err != nil {
			continue
		}
		candidate.Reaction = reaction
		distance := math.Abs(bar.Close - level)
		if best == nil || candidate.Quality.Overall > best.Quality.Overall || (candidate.Quality.Overall == best.Quality.Overall && distance < bestDistance) {
			copy := candidate
			best, bestDistance = &copy, distance
		}
	}
	if best == nil {
		return nil
	}
	return []opportunity.Candidate{*best}
}

func gradeScore(grade string) float64 {
	if grade == "A" {
		return 1
	}
	return 0
}
