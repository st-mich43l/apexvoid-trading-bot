// Package momentumride restores the frozen Python momentum-ride thesis: a
// closed bar with a strong body that breaks the latest opposite-side swing,
// taken near a scored zone (or the nearest valid-side key level). The decision
// runs on the engine's detector-contract frame so direction, premium/discount,
// chop, zone selection and confluence qualification are the frozen detector's.
package momentumride

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "momentum_ride"
const Version = "v2"

type Strategy struct {
	minimumBodyFraction, maximumOverlap, invalidationATR float64
	minimumTargetATR, expiryHours                        float64
	requireMomentumVA                                    bool
	momentumOppositionTolerance                          float64
	detector                                             strategyutil.LegacyDetectorSettings
	fingerprint                                          string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("momentumride: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	for key, dst := range map[string]*float64{"minimum_body_fraction": &s.minimumBodyFraction, "maximum_overlap_fraction": &s.maximumOverlap, "invalidation_buffer_atr": &s.invalidationATR, "minimum_target_distance_atr": &s.minimumTargetATR, "expiry_hours": &s.expiryHours, "momentum_opposition_tolerance": &s.momentumOppositionTolerance} {
		v, err := strategyutil.Float(c.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	var ok bool
	if s.requireMomentumVA, ok = c.Parameters["require_momentum_va"].(bool); !ok {
		return nil, fmt.Errorf("momentumride: require_momentum_va must be boolean")
	}
	var err error
	if s.detector, err = strategyutil.ParseLegacyDetectorSettings(c.Parameters); err != nil {
		return nil, err
	}
	if s.minimumBodyFraction <= 0 || s.minimumBodyFraction > 1 || s.maximumOverlap < 0 || s.maximumOverlap >= 1 || s.invalidationATR <= 0 || s.minimumTargetATR <= 0 || s.expiryHours <= 0 || s.momentumOppositionTolerance < 0 {
		return nil, fmt.Errorf("momentumride: invalid parameters")
	}
	return s, nil
}

func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	direction := strategyutil.StructuralDirection(ctx)
	if tf == nil || !direction.IsValid() || strategyutil.InChop(tf) || !strategyutil.PremiumDiscountAllows(tf, direction, false) {
		return nil
	}
	d, ok := strategyutil.NewLegacyDetector(ctx, market.M5, direction, s.detector)
	if !ok {
		return nil
	}
	if !d.StrongBodyBreak(s.minimumBodyFraction) {
		return nil
	}
	if s.requireMomentumVA && !momentumAllows(d.Frame, direction, s.momentumOppositionTolerance) {
		return nil
	}
	bar := d.Frame.Bars[len(d.Frame.Bars)-1]
	broken, hasBroken := latestImpulseSwing(d.Frame.Swings, direction)

	var (
		zone            techniquezone.Zone
		level           float64
		touches         int
		structuralAgree bool
		anchorID        string
		location        = "zone"
	)
	if selected, _, found := d.BestValidZone(d.CandidateZones()); found {
		zone, level, touches, structuralAgree, anchorID = selected, d.ZoneKey(selected), selected.Touches, true, d.ZoneID(selected)
	} else {
		nearest, hasLevel := d.NearestLevel()
		if !hasLevel {
			return nil
		}
		zone, level, touches, anchorID, location = d.EntryZone(nearest.Price), nearest.Price, nearest.Touches, strategyutil.LevelID(nearest), "key_level"
	}
	factors := confluence.Factors{HTFAligned: d.HTFAligned(), Touches: touches, DisplacementGrade: true, StructuralAgreement: structuralAgree}
	result := d.Finish(level, zone, factors, "", nil, nil)
	if result == nil {
		return nil
	}

	low, high := result.Zone.Low(), result.Zone.High()
	invalid, reference := low-s.invalidationATR*d.ATR, high
	if direction == market.Sell {
		invalid, reference = high+s.invalidationATR*d.ATR, low
	}
	target, targetLabel := reference+s.minimumTargetATR*d.ATR, "momentum_extension"
	if direction == market.Sell {
		target = reference - s.minimumTargetATR*d.ATR
	}
	if pool, found := nearestPool(d, direction, reference, s.minimumTargetATR*d.ATR); found {
		target, targetLabel = pool, "opposing_liquidity"
	}
	overlap := recentOverlap(d.Frame.Bars, d.ATR)
	continuity := strategyutil.Clamp01(1 - overlap/math.Max(s.maximumOverlap, .01))
	body := math.Abs(bar.Close - bar.Open)
	impulse := strategyutil.Clamp01(body / math.Max(d.ATR, .0000001))
	quality := strategyutil.Clamp01(.5*impulse + .3*continuity + .2)
	brokenKey := "none"
	formedAt := bar.Time
	if hasBroken {
		brokenKey = fmt.Sprintf("%s:%.8f", broken.Kind, broken.Price)
		formedAt = d.Frame.Bars[broken.Index].Time
	}
	evidence := []string{"legacy_structural_impulse_break", "broken_swing_" + brokenKey, "legacy_pd_location", "structural_source_" + location, "momentum_va_aligned"}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: "momentum:" + brokenKey + ":" + anchorID, Symbol: ctx.Symbol, Direction: direction,
		EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "momentum_structure_failed",
		Target: target, TargetLabel: targetLabel, Evidence: evidence,
		Quality: opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{
			"structural_break": 1, "displacement": impulse, "continuity": continuity, "location": 1,
		}},
		FormedAt: formedAt, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return nil
	}
	candidate.DetectorConfluence = result.ConfluenceContext()
	return []opportunity.Candidate{candidate}
}

// latestImpulseSwing mirrors _broken_impulse_swing: the latest swing high for a
// buy, the latest swing low for a sell.
func latestImpulseSwing(swings []techniquezone.Swing, direction market.Direction) (techniquezone.Swing, bool) {
	want := "low"
	if direction == market.Buy {
		want = "high"
	}
	for i := len(swings) - 1; i >= 0; i-- {
		if swings[i].Kind == want {
			return swings[i], true
		}
	}
	return techniquezone.Swing{}, false
}

// momentumAllows mirrors _momentum_va_allows: velocity aligned with the
// direction and acceleration not strongly opposing.
func momentumAllows(frame *analysiscontext.LegacyFrame, direction market.Direction, tolerance float64) bool {
	if direction == market.Buy {
		return frame.Momentum == momentum.Bull && frame.MomentumAcceleration >= -tolerance
	}
	return frame.Momentum == momentum.Bear && frame.MomentumAcceleration <= tolerance
}

// nearestPool is the nearest pool beyond the entry in the trade direction at
// least minimumDistance away.
func nearestPool(d *strategyutil.LegacyDetector, direction market.Direction, from, minimumDistance float64) (float64, bool) {
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

func recentOverlap(candles []market.Candle, atr float64) float64 {
	if len(candles) < 2 {
		return 1
	}
	a, b := candles[len(candles)-2], candles[len(candles)-1]
	return math.Max(0, math.Min(a.High, b.High)-math.Max(a.Low, b.Low)) / math.Max(math.Min(a.Range(), b.Range()), atr)
}
