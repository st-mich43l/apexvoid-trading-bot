// Package fadescalp implements the liquidity-sweep fade: price runs through a pool of
// equal highs or lows, the pool's stops are taken, the market closes back and shows a
// confirmed reaction, and the trade is the reversion toward the local mean.
//
// The thesis is measured, not named. For every candidate the strategy reads the
// extension (how far the sweep ran from the local mean, in ATR), the sweep candle's
// body and rejection wick, whether the move continued after the sweep, and the room to
// the nearest structural objective. A fade is published only if the sweep was reclaimed
// and has not been followed by continuation, the reaction confirmed, the invalidation
// sits beyond the sweep's own extreme, and the nearest structural objective offers at
// least the configured reward:risk. A strong displacement that is not reclaimed is never
// faded, and there is no invented target: with no structural objective there is no
// opportunity. The decision runs on the engine's detector-contract frame, so the
// direction, premium/discount gate, chop gate, sweep grade and confluence qualification
// are the frozen detector's. See docs/strategies/fade_scalp.md.
package fadescalp

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "fade_scalp"

// Version v3 replaces v2, which invented a 2R target when no structural objective
// existed, set the rejection factor on every decision and put the stop inside the
// sweep's wick.
const Version = "v3"

type Strategy struct {
	invalidationATR, minimumRewardRisk, expiryHours float64
	chopEdgeFraction                                float64
	strictPD                                        bool
	detector                                        strategyutil.LegacyDetectorSettings
	fingerprint                                     string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("fadescalp: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*float64{
		"invalidation_buffer_atr": &s.invalidationATR, "minimum_reward_risk": &s.minimumRewardRisk,
		"expiry_hours": &s.expiryHours, "chop_edge_fraction": &s.chopEdgeFraction,
	} {
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
	if s.detector, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.invalidationATR <= 0 || s.minimumRewardRisk <= 0 || s.expiryHours <= 0 || s.chopEdgeFraction < 0 || s.chopEdgeFraction > .5 {
		return nil, fmt.Errorf("fadescalp: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

// Objective is the structural price a fade aims at.
type Objective struct {
	Price float64
	Label string
}

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	direction := strategyutil.StructuralDirection(ctx)
	if tf == nil || !direction.IsValid() || !strategyutil.PremiumDiscountAllows(tf, direction, s.strictPD) {
		return nil
	}
	d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, direction, s.detector)
	if !ok {
		return nil
	}
	wantSide, wantKind := "sell", "equal_low"
	if direction == market.Sell {
		wantSide, wantKind = "buy", "equal_high"
	}

	var best *opportunity.Candidate
	var bestResult *strategyutil.LegacyResult
	bestDistance := math.Inf(1)
	for _, pool := range d.Frame.Pools {
		// Equal levels are the pools with at least two touches.
		if pool.Touches < 2 || pool.Side != wantSide {
			continue
		}
		grab := d.LevelGrab(pool.Level, pool.Band)
		if grab == nil || grab.Grade != "A" && grab.Grade != "B" || grab.Index < 0 || grab.Index >= len(d.Frame.Bars) {
			continue
		}
		zone := d.EntryZone(pool.Level)
		if strategyutil.InChop(tf) && (grab.Grade != "A" || !strategyutil.ChopEdgeAllows(tf, direction, zone.Low(), zone.High(), s.chopEdgeFraction)) {
			continue
		}
		confirmation := d.Reaction(zone.Low(), zone.High(), grab)
		if confirmation == nil {
			continue
		}
		sweep := d.Frame.Bars[grab.Index]
		// The sweep failed to continue: nothing after it has traded beyond its extreme. A
		// market that keeps running through the swept level is accepting it, not reverting.
		if continued(d, direction, grab.Index) {
			continue
		}
		factors := strategyutil.FactorsForConfirmation(confluence.Factors{
			HTFAligned: d.HTFAligned(), Touches: pool.Touches,
			WickRejection:     reaction.WickRejection(sweep, string(direction)),
			DisplacementGrade: grab.ReactionDisplacement,
		}, confirmation.Type)
		structuralLow, structuralHigh := zone.Low(), zone.High()
		result := d.Finish(pool.Level, zone, factors, wantKind, &structuralLow, &structuralHigh)
		if result == nil {
			continue
		}

		low, high := result.Zone.Low(), result.Zone.High()
		// The thesis fails when the sweep's own extreme is exceeded, so the stop sits beyond it
		// (and beyond the entry band, whichever reaches further).
		invalid, reference := math.Min(low, sweep.Low)-s.invalidationATR*d.ATR, high
		if direction == market.Sell {
			invalid, reference = math.Max(high, sweep.High)+s.invalidationATR*d.ATR, low
		}
		risk := math.Abs(reference - invalid)
		objective, found := s.objective(d, tf, direction, reference)
		if !found || risk <= 0 {
			continue
		}
		reward := math.Abs(objective.Price - reference)
		if reward/risk < s.minimumRewardRisk {
			continue
		}
		mean, _, hasMean := strategyutil.MeanReference(tf)
		extension := 0.0
		if hasMean && d.ATR > 0 {
			extension = math.Max(0, math.Abs(mean-sweepExtreme(sweep, direction))/d.ATR)
		}
		evidence := []string{"equal_level_" + wantKind, "liquidity_grab_grade_" + grab.Grade, "legacy_pd_location", "structural_reaction_" + confirmation.Type, "sweep_not_continued"}
		if strategyutil.InChop(tf) {
			evidence = append(evidence, "chop_edge_grade_a")
		}
		sweepTime := sweep.Time
		formedAt := sweepTime
		if formedAt > confirmation.ConfirmationTime {
			formedAt = confirmation.TouchTime
		}
		stars := float64(result.Stars)
		components := map[string]float64{
			"confluence_stars":      stars,
			"extension_atr":         extension,
			"sweep_body_ratio":      sweep.BodyRatio(),
			"sweep_upper_wick":      sweep.UpperWickRatio(),
			"sweep_lower_wick":      sweep.LowerWickRatio(),
			"sweep_penetration_atr": penetrationATR(sweep, pool.Level, direction, d.ATR),
			"equal_level_touches":   float64(pool.Touches),
			"reward_risk":           reward / risk,
			"target_room_atr":       reward / math.Max(d.ATR, 1e-12),
		}
		if grab.ReactionDisplacement {
			components["reaction_displacement"] = 1
		}
		setupKey := fmt.Sprintf("fade:%s:%.8f", wantKind, pool.Level)
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "sweep_extreme_exceeded",
			Target: objective.Price, TargetLabel: objective.Label, Evidence: evidence,
			Quality:  opportunity.StrategyQuality{Overall: strategyutil.Clamp01(stars / 3), Components: components},
			FormedAt: formedAt, ConfirmedAt: confirmation.ConfirmationTime, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		})
		if err != nil {
			continue
		}
		candidate.Reaction = &opportunity.ReactionConfirmation{
			ZoneID: setupKey, TouchBarTime: confirmation.TouchTime, ConfirmationBarTime: confirmation.ConfirmationTime,
			ReactionType: "rejection", Pattern: confirmation.Type,
		}
		candidate.DetectorConfluence = result.ConfluenceContext()
		distance := d.ZoneDistance(result.Zone)
		if best == nil || result.Stars > bestResult.Stars || result.Stars == bestResult.Stars && distance < bestDistance {
			copy := candidate
			best, bestResult, bestDistance = &copy, result, distance
		}
	}
	if best == nil {
		return nil
	}
	return []opportunity.Candidate{*best}
}

// sweepExtreme is the price the sweep ran to: the low of a sell-side sweep (a BUY fade) or
// the high of a buy-side one.
func sweepExtreme(sweep market.Candle, direction market.Direction) float64 {
	if direction == market.Buy {
		return sweep.Low
	}
	return sweep.High
}

// penetrationATR is how far the sweep candle ran beyond the pool's level, in ATR.
func penetrationATR(sweep market.Candle, level float64, direction market.Direction, atr float64) float64 {
	depth := level - sweep.Low
	if direction == market.Sell {
		depth = sweep.High - level
	}
	if atr <= 0 || depth < 0 {
		return 0
	}
	return depth / atr
}

// continued reports whether any candle after the sweep traded beyond the sweep's extreme:
// the move was not exhausted, it carried on.
func continued(d *strategyutil.LegacyDetector, direction market.Direction, sweepIndex int) bool {
	extreme := sweepExtreme(d.Frame.Bars[sweepIndex], direction)
	for _, bar := range d.Frame.Bars[sweepIndex+1:] {
		if direction == market.Buy && bar.Low < extreme-d.Settings.PipSize || direction == market.Sell && bar.High > extreme+d.Settings.PipSize {
			return true
		}
	}
	return false
}

// objective is the NEAREST structural price beyond the entry in the trade direction: the
// local mean (the established range's equilibrium, else the dealing range's) or the nearest
// opposing liquidity pool, whichever is closer. The nearest decides: an objective that
// leaves too little room is not skipped in favour of a farther one the market would have to
// travel through it to reach.
func (s *Strategy) objective(d *strategyutil.LegacyDetector, tf *analysiscontext.TimeframeContext, direction market.Direction, reference float64) (Objective, bool) {
	ahead := func(price float64) float64 {
		if direction == market.Buy {
			return price - reference
		}
		return reference - price
	}
	var best Objective
	bestDistance := math.Inf(1)
	if mean, source, ok := strategyutil.MeanReference(tf); ok && ahead(mean) > 0 {
		best, bestDistance = Objective{Price: mean, Label: source}, ahead(mean)
	}
	want := "buy"
	if direction == market.Sell {
		want = "sell"
	}
	for _, pool := range d.Frame.Pools {
		if pool.Side != want {
			continue
		}
		if distance := ahead(pool.Level); distance > 0 && distance < bestDistance {
			best, bestDistance = Objective{Price: pool.Level, Label: "opposing_liquidity"}, distance
		}
	}
	return best, !math.IsInf(bestDistance, 1)
}
