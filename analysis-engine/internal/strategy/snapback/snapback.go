// Package snapback restores the frozen Python Snap Back thesis on top of the
// canonical Go structure/zone/liquidity books.
package snapback

import (
	"fmt"
	"math"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

const ID strategy.StrategyID = "snap_back"
const Version = "v2"

type Strategy struct {
	extensionATR, invalidationATR, targetR, expiryHours float64
	maximumEntryATR, proximalBandATR                    float64
	extensionSource                                     string
	strictPD                                            bool
	reaction                                            strategyutil.ReactionConfig
	fingerprint                                         string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("snapback: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	for key, dst := range map[string]*float64{
		"extension_atr": &s.extensionATR, "invalidation_buffer_atr": &s.invalidationATR,
		"target_r": &s.targetR, "expiry_hours": &s.expiryHours, "maximum_entry_atr": &s.maximumEntryATR,
		"proximal_band_atr": &s.proximalBandATR,
	} {
		v, err := strategyutil.Float(cfg.Parameters, key)
		if err != nil {
			return nil, err
		}
		*dst = v
	}
	var ok bool
	if s.extensionSource, ok = cfg.Parameters["extension_source"].(string); !ok || (s.extensionSource != "impulse" && s.extensionSource != "zone") {
		return nil, fmt.Errorf("snapback: extension_source must be impulse or zone")
	}
	if s.strictPD, ok = cfg.Parameters["strict_premium_discount"].(bool); !ok {
		return nil, fmt.Errorf("snapback: strict_premium_discount must be boolean")
	}
	var err error
	if s.reaction, err = strategyutil.ParseReactionConfig(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.extensionATR <= 0 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 || s.maximumEntryATR <= 0 || s.proximalBandATR <= 0 {
		return nil, fmt.Errorf("snapback: invalid parameters")
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

	low, high, structuralID, formedAt, touches := 0.0, 0.0, "", int64(0), 0
	structuralSource := "supply_demand"
	zones := strategyutil.LiveZones(tf, direction, bar.Close, atr, s.maximumEntryATR)
	if len(zones) > 0 {
		z := zones[0]
		low, high, structuralID, formedAt, touches = float64(z.Low), float64(z.High), z.ID, z.OriginTime, z.TouchCount
	} else if level := strategyutil.NearestValidLevel(tf, direction, bar.Close); level != nil {
		low, high = strategyutil.EntryBandForLevel(*level, atr, s.proximalBandATR)
		structuralID, formedAt, touches, structuralSource = level.ID, level.AnchorTime, level.Touches, "key_level"
	} else {
		return nil
	}

	distance := distanceFromSource(tf, direction, bar.Close, low, high, s.extensionSource)
	if distance < s.extensionATR*atr {
		return nil
	}
	grab := strategyutil.GrabForBand(tf, direction, low, high, 0)
	if grab == nil {
		return nil
	}
	reaction := strategyutil.ConfirmReaction(tf, structuralID, direction, low, high, atr, formedAt, s.reaction)
	if reaction == nil {
		return nil
	}

	invalid := low - s.invalidationATR*atr
	entryReference := high
	if direction == market.Sell {
		invalid, entryReference = high+s.invalidationATR*atr, low
	}
	risk := math.Abs(entryReference - invalid)
	target := entryReference + s.targetR*risk
	if direction == market.Sell {
		target = entryReference - s.targetR*risk
	}
	quality := strategyutil.Clamp01(.45 + .1*math.Min(float64(touches), 3) + .15*boolScore(grab.Grade == "A") + .1*strategyutil.Clamp01(distance/(s.extensionATR*atr)-1))
	evidence := []string{"legacy_snap_extension_" + s.extensionSource, "legacy_pd_location", "liquidity_grab_grade_" + grab.Grade, "structural_reaction_" + reaction.Pattern, "structural_source_" + structuralSource}
	c, err := strategyutil.Candidate(strategyutil.CandidateSpec{ID: string(ID), Version: Version, SetupKey: "snap:" + structuralID, Symbol: ctx.Symbol, Direction: direction, EntryLow: low, EntryHigh: high, Invalidation: invalid, InvalidationLabel: "snap_back_structure_failed", Target: target, TargetLabel: "snap_back_reversion", Evidence: evidence, Quality: opportunity.StrategyQuality{Overall: quality, Components: map[string]float64{"extension": strategyutil.Clamp01(distance / (s.extensionATR * atr)), "liquidity_grade": .75 + .25*boolScore(grab.Grade == "A"), "reaction": 1}}, FormedAt: formedAt, ConfirmedAt: reaction.ConfirmationBarTime, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint})
	if err != nil {
		return nil
	}
	c.Reaction = reaction
	return []opportunity.Candidate{c}
}

func distanceFromSource(tf *analysiscontext.TimeframeContext, direction market.Direction, price, low, high float64, source string) float64 {
	if source == "zone" {
		return distanceToBand(price, low, high)
	}
	for i := len(tf.Structure.Swings) - 1; i >= 0; i-- {
		sw := tf.Structure.Swings[i]
		if direction == market.Buy && sw.Kind.String() == "low" {
			return math.Max(0, price-float64(sw.Price))
		}
		if direction == market.Sell && sw.Kind.String() == "high" {
			return math.Max(0, float64(sw.Price)-price)
		}
	}
	return distanceToBand(price, low, high)
}

func distanceToBand(price, low, high float64) float64 {
	if price < low {
		return low - price
	}
	if price > high {
		return price - high
	}
	return 0
}

func boolScore(v bool) float64 {
	if v {
		return 1
	}
	return 0
}
