// Package fadescalp implements the equal-level sweep reversal thesis of the
// frozen Python fade_scalp detector: price sweeps a pool of equal highs or
// lows, reclaims it and shows a structural reaction. The decision runs on the
// engine's detector-contract frame so the direction, premium/discount gate,
// chop gate, sweep grade and confluence qualification are the frozen
// detector's.
package fadescalp

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "fade_scalp"
const Version = "v2"

type Strategy struct {
	invalidationATR, targetR, expiryHours float64
	chopEdgeFraction                      float64
	strictPD                              bool
	detector                              strategyutil.LegacyDetectorSettings
	fingerprint                           string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("fadescalp: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*float64{"invalidation_buffer_atr": &s.invalidationATR, "target_r": &s.targetR, "expiry_hours": &s.expiryHours, "chop_edge_fraction": &s.chopEdgeFraction} {
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
	if s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 || s.chopEdgeFraction < 0 || s.chopEdgeFraction > .5 {
		return nil, fmt.Errorf("fadescalp: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

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
		if grab == nil || grab.Grade != "A" && grab.Grade != "B" {
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
		factors := strategyutil.FactorsForConfirmation(confluence.Factors{
			HTFAligned: d.HTFAligned(), Touches: pool.Touches, WickRejection: true, DisplacementGrade: grab.Grade == "A",
		}, confirmation.Type)
		structuralLow, structuralHigh := zone.Low(), zone.High()
		result := d.Finish(pool.Level, zone, factors, wantKind, &structuralLow, &structuralHigh)
		if result == nil {
			continue
		}

		low, high := result.Zone.Low(), result.Zone.High()
		invalid, reference := low-s.invalidationATR*d.ATR, high
		if direction == market.Sell {
			invalid, reference = high+s.invalidationATR*d.ATR, low
		}
		risk := math.Abs(reference - invalid)
		target := reference + s.targetR*risk
		if direction == market.Sell {
			target = reference - s.targetR*risk
		}
		if liqTarget, found := opposingPool(d, direction, reference, risk); found {
			target = liqTarget
		}
		quality := strategyutil.Clamp01(.55 + .15*gradeScore(grab.Grade) + .05*math.Min(float64(pool.Touches), 3))
		evidence := []string{"equal_level_" + wantKind, "liquidity_grab_grade_" + grab.Grade, "legacy_pd_location", "structural_reaction_" + confirmation.Type}
		if strategyutil.InChop(tf) {
			evidence = append(evidence, "chop_edge_grade_a")
		}
		sweepTime := d.Frame.Bars[grab.Index].Time
		formedAt := sweepTime
		if formedAt > confirmation.ConfirmationTime {
			formedAt = confirmation.TouchTime
		}
		setupKey := fmt.Sprintf("fade:%s:%.8f", wantKind, pool.Level)
		candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "equal_level_reclaim_failed",
			Target: target, TargetLabel: "fade_opposing_liquidity", Evidence: evidence,
			Quality: opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{
				"liquidity_grade": .75 + .25*gradeScore(grab.Grade), "reaction": 1, "location": 1,
			}},
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

// opposingPool is the nearest unswept pool beyond the entry in the trade
// direction at least minimumDistance away.
func opposingPool(d *strategyutil.LegacyDetector, direction market.Direction, from, minimumDistance float64) (float64, bool) {
	want := "buy"
	if direction == market.Sell {
		want = "sell"
	}
	best, bestDistance := 0.0, math.Inf(1)
	for _, pool := range d.Frame.Pools {
		if pool.Side != want {
			continue
		}
		distance := pool.Level - from
		if direction == market.Sell {
			distance = from - pool.Level
		}
		if distance >= minimumDistance && distance < bestDistance {
			best, bestDistance = pool.Level, distance
		}
	}
	return best, !math.IsInf(bestDistance, 1)
}

func gradeScore(grade string) float64 {
	if grade == "A" {
		return 1
	}
	return 0
}
