// Package momentumride restores the Python structural impulse continuation
// thesis. Three same-colour candles are only secondary quality evidence; a
// real body break of a canonical swing is the activation fact.
package momentumride

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

const ID strategy.StrategyID = "momentum_ride"
const Version = "v2"

type Strategy struct {
	minimumBodyFraction, maximumOverlap, invalidationATR float64
	minimumTargetATR, expiryHours, maximumEntryATR       float64
	proximalBandATR                                      float64
	requireMomentumVA                                    bool
	momentumOppositionTolerance                          float64
	fingerprint                                          string
}

func New(c strategy.Config) (strategy.Strategy, error) {
	if c.ID != ID {
		return nil, fmt.Errorf("momentumride: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(c.ID), c.Version, c.Parameters)}
	for key, dst := range map[string]*float64{"minimum_body_fraction": &s.minimumBodyFraction, "maximum_overlap_fraction": &s.maximumOverlap, "invalidation_buffer_atr": &s.invalidationATR, "minimum_target_distance_atr": &s.minimumTargetATR, "expiry_hours": &s.expiryHours, "maximum_entry_atr": &s.maximumEntryATR, "proximal_band_atr": &s.proximalBandATR, "momentum_opposition_tolerance": &s.momentumOppositionTolerance} {
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
	if s.minimumBodyFraction <= 0 || s.minimumBodyFraction > 1 || s.maximumOverlap < 0 || s.maximumOverlap >= 1 || s.invalidationATR <= 0 || s.minimumTargetATR <= 0 || s.expiryHours <= 0 || s.maximumEntryATR <= 0 || s.proximalBandATR <= 0 || s.momentumOppositionTolerance < 0 {
		return nil, fmt.Errorf("momentumride: invalid parameters")
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
	if tf == nil || !ok || atr <= 0 || !direction.IsValid() || tf.Regime.Kind == "chop" || !strategyutil.PremiumDiscountAllows(tf, direction, false) {
		return nil
	}
	if bar.Range() <= 0 || bar.Body() < s.minimumBodyFraction*bar.Range() || direction == market.Buy && !bar.IsBullish() || direction == market.Sell && !bar.IsBearish() {
		return nil
	}
	broken := latestImpulseSwing(tf.Structure.Swings, direction)
	if broken == nil || direction == market.Buy && bar.Close <= float64(broken.Price) || direction == market.Sell && bar.Close >= float64(broken.Price) {
		return nil
	}
	if s.requireMomentumVA && !momentumAllows(tf.Momentum, direction, s.momentumOppositionTolerance) {
		return nil
	}

	low, high, anchorID := 0.0, 0.0, ""
	location := "zone"
	if zones := strategyutil.LiveZones(tf, direction, bar.Close, atr, s.maximumEntryATR); len(zones) > 0 {
		low, high, anchorID = float64(zones[0].Low), float64(zones[0].High), zones[0].ID
	} else if level := strategyutil.NearestValidLevel(tf, direction, bar.Close); level != nil {
		low, high = strategyutil.EntryBandForLevel(*level, atr, s.proximalBandATR)
		anchorID, location = level.ID, "key_level"
	} else {
		return nil
	}
	invalid, reference := low-s.invalidationATR*atr, high
	if direction == market.Sell {
		invalid, reference = high+s.invalidationATR*atr, low
	}
	target, found := strategyutil.OpposingLiquidity(tf.Liquidity.Pools, direction, reference, s.minimumTargetATR*atr)
	if !found {
		return nil
	}
	overlap := recentOverlap(tf.Candles, atr)
	continuity := strategyutil.Clamp01(1 - overlap/math.Max(s.maximumOverlap, .01))
	impulse := strategyutil.Clamp01(bar.Body() / math.Max(atr, .0000001))
	quality := strategyutil.Clamp01(.5*impulse + .3*continuity + .2)
	evidence := []string{"legacy_structural_impulse_break", "broken_swing_" + broken.ID, "legacy_pd_location", "structural_source_" + location, "momentum_va_aligned"}
	c, err := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: "momentum:" + broken.ID + ":" + anchorID, Symbol: ctx.Symbol, Direction: direction, EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "momentum_structure_failed", Target: float64(target), TargetLabel: "opposing_liquidity", Evidence: evidence, Quality: opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{"structural_break": 1, "displacement": impulse, "continuity": continuity, "location": 1}}, FormedAt: broken.Time, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if err != nil {
		return nil
	}
	return []opportunity.Candidate{c}
}

func latestImpulseSwing(swings []structure.Swing, direction market.Direction) *structure.Swing {
	want := structure.SwingHigh
	if direction == market.Sell {
		want = structure.SwingLow
	}
	for i := len(swings) - 1; i >= 0; i-- {
		if swings[i].Kind == want {
			return &swings[i]
		}
	}
	return nil
}

func momentumAllows(result momentum.Result, direction market.Direction, tolerance float64) bool {
	if direction == market.Buy {
		return result.State == momentum.Bull && result.Acceleration >= -tolerance
	}
	return result.State == momentum.Bear && result.Acceleration <= tolerance
}

func recentOverlap(candles []market.Candle, atr float64) float64 {
	if len(candles) < 2 {
		return 1
	}
	a, b := candles[len(candles)-2], candles[len(candles)-1]
	return math.Max(0, math.Min(a.High, b.High)-math.Max(a.Low, b.Low)) / math.Max(math.Min(a.Range(), b.Range()), atr)
}
