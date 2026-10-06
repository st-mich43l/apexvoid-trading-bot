// Package liquiditysweep implements the sweep of a lone-extreme liquidity pool:
// price trades through the highest swing high (or lowest swing low) of the
// window by the frozen equal-level band and a displacement candle closes back
// through it. It is distinct from extension-based Snap Back and from Fade Scalp,
// which owns the equal-level pools (two or more touches).
//
// It is founded on the frozen detector contract so that it separates a liquidity
// event from the ordinary XAU wick the way the profitable detectors did:
//
//   - the pool is one the frozen liquidity_pools() kept, over the frozen bounded
//     window (a single swing is not liquidity unless it is the extreme of its
//     side);
//   - the sweep must clear the pool by the frozen band (equal_tol_atr), close back
//     through the level and be graded A or B by the frozen grab grade (a marginal
//     close, grade C, is not a sweep) on the bar being evaluated;
//   - the premium/discount location must fit a reversal;
//   - the reaction must reach the shared confluence floor with the higher-timeframe
//     alignment, touch count and displacement the frozen detectors scored.
//
// None of those thresholds is new; they are the frozen contract's.
package liquiditysweep

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "liquidity_sweep"
const Version = "v3"

type Strategy struct {
	minimumBodyATR, invalidationATR, targetR, expiryHours float64
	strictPD                                              bool
	detector                                              strategyutil.LegacyDetectorSettings
	fingerprint                                           string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("liquiditysweep: wrong ID")
	}
	s := &Strategy{fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}
	vals := []*float64{&s.minimumBodyATR, &s.invalidationATR, &s.targetR, &s.expiryHours}
	keys := []string{"minimum_rejection_body_atr", "invalidation_buffer_atr", "target_r", "expiry_hours"}
	for i, k := range keys {
		v, e := strategyutil.Float(cfg.Parameters, k)
		if e != nil {
			return nil, e
		}
		*vals[i] = v
	}
	var err error
	var ok bool
	if s.strictPD, ok = cfg.Parameters["strict_premium_discount"].(bool); !ok {
		return nil, fmt.Errorf("liquiditysweep: strict_premium_discount must be boolean")
	}
	if s.detector, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, err
	}
	if s.minimumBodyATR <= 0 || s.invalidationATR <= 0 || s.targetR <= 0 || s.expiryHours <= 0 {
		return nil, fmt.Errorf("liquiditysweep: invalid parameters")
	}
	return s, nil
}
func (s *Strategy) ID() strategy.StrategyID                { return ID }
func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *analysiscontext.MarketContext) []opportunity.Candidate {
	tf := ctx.Timeframes[market.M5]
	bar, ok := strategyutil.LastBar(ctx, market.M5)
	if tf == nil || !ok || tf.Legacy == nil || len(tf.Legacy.Bars) == 0 {
		return nil
	}
	frame := tf.Legacy
	last := len(frame.Bars) - 1
	atr := ctx.Volatility.ATR
	if atr <= 0 || bar.Body() < s.minimumBodyATR*atr {
		return nil
	}
	var out []opportunity.Candidate
	for _, grab := range frame.Grabs {
		// The grab is on the bar being evaluated, graded A or B, and the pool is a
		// lone extreme: equal levels are Fade Scalp's.
		if grab.Index != last || grab.Grade != "A" && grab.Grade != "B" || grab.Pool.Touches >= 2 || grab.Inducement {
			continue
		}
		direction := market.Buy
		if grab.Direction == "bear" {
			direction = market.Sell
		}
		if direction == market.Buy && !bar.IsBullish() || direction == market.Sell && !bar.IsBearish() {
			continue
		}
		if !strategyutil.PremiumDiscountAllows(tf, direction, s.strictPD) {
			continue
		}
		d, found := strategyutil.NewLegacyDetector(ctx, market.M5, direction, s.detector)
		if !found {
			continue
		}
		zone := techniquezone.Zone{Bottom: math.Min(bar.Open, bar.Close), Top: math.Max(bar.Open, bar.Close), Side: "demand", Source: "liquidity_sweep", BreakIndex: -1}
		if direction == market.Sell {
			zone.Side = "supply"
		}
		factors := confluence.Factors{
			HTFAligned: d.HTFAligned(), Touches: grab.Pool.Touches, WickRejection: true, DisplacementGrade: grab.Grade == "A",
		}
		level := grab.Pool.Level
		result := d.Finish(level, zone, factors, "liquidity_pool", nil, nil)
		if result == nil {
			continue
		}
		// The stop sits beyond the sweep extreme (the reclaim bar's wick), and
		// beyond the entry body whenever the body reaches past it.
		buffer := s.invalidationATR * atr
		invalid := bar.Low - buffer
		if direction == market.Sell {
			invalid = bar.High + buffer
		}
		entry := bar.Close
		risk := math.Abs(entry - invalid)
		target := entry + s.targetR*risk
		if direction == market.Sell {
			target = entry - s.targetR*risk
		}
		q := strategyutil.Clamp01(bar.Body() / atr)
		setupKey := fmt.Sprintf("sweep:%s:%.8f", direction, level)
		c, e := strategyutil.Candidate(strategyutil.CandidateSpec{
			ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
			EntryLow: math.Min(bar.Open, bar.Close), EntryHigh: math.Max(bar.Open, bar.Close),
			Invalidation: invalid, InvalidationLabel: "sweep_extreme_failed", Target: target, TargetLabel: "sweep_reversion_objective",
			Evidence: []string{"m5_liquidity_pool_swept", "m5_sweep_reclaimed", "m5_opposite_displacement"},
			Quality:  opportunity.StrategyQuality{Overall: q, Components: map[string]float64{"rejection_displacement": q, "reclaim": 1}},
			FormedAt: bar.Time, ConfirmedAt: bar.Time, ExpiryHours: s.expiryHours, Fingerprint: s.fingerprint,
		})
		if e == nil {
			c.DetectorConfluence = result.ConfluenceContext()
			out = append(out, c)
		}
	}
	return out
}
