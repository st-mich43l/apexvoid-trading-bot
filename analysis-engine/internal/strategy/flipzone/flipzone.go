// Package flipzone publishes the frozen flip_demand_zone_reaction /
// flip_supply_zone_reaction decisions: a confirmed reaction off a zone born of a
// role flip — a broken level accepted from the new side — and only while the
// level's own role still agrees with the trade.
//
// An ordinary zone touch is not a flip. The zone must carry the flip source, be
// unmitigated and entry-valid; the key level it is anchored to must classify, on
// the closed bars, as broken resistance (BUY) or broken support (SELL); and the
// zone must then show a confirmed structural reaction that clears the shared
// confluence floor. One candidate per side at most.
package flipzone

import (
	"fmt"
	"math"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "flip_zone"
const Version = "v3"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is FlipZone's own parsed technical configuration.
type Config struct {
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
	// BreakoutAcceptBars is key_level_role.py's breakout_accept_bars.
	BreakoutAcceptBars int
	// Legacy qualifies the reaction through the frozen detector contract.
	Legacy strategyutil.LegacyDetectorSettings
}

// Strategy is FlipZoneStrategy.
type Strategy struct {
	cfg         Config
	fingerprint string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("flipzone: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	var out Config
	var err error
	for key, dst := range map[string]*float64{
		"invalidation_buffer_atr":     &out.InvalidationBufferATR,
		"minimum_target_distance_atr": &out.MinimumTargetDistanceATR,
		"expiry_hours":                &out.ExpiryHours,
	} {
		if *dst, err = strategyutil.Float(cfg.Parameters, key); err != nil {
			return nil, fmt.Errorf("flipzone: %w", err)
		}
	}
	if out.BreakoutAcceptBars, err = strategyutil.Int(cfg.Parameters, "breakout_accept_bars"); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	if out.Legacy, err = strategyutil.ParseLegacyDetectorSettings(cfg.Parameters); err != nil {
		return nil, fmt.Errorf("flipzone: %w", err)
	}
	if out.InvalidationBufferATR <= 0 || out.ExpiryHours <= 0 || out.BreakoutAcceptBars < 1 {
		return nil, fmt.Errorf("flipzone: invalidation_buffer_atr, expiry_hours and breakout_accept_bars must be positive")
	}
	return &Strategy{cfg: out, fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

func (s *Strategy) RequiredTimeframes() []market.Timeframe { return []market.Timeframe{market.M5} }

func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	base, ok := strategyutil.NewLegacyDetectorForFrame(ctx, market.M5, s.cfg.Legacy)
	if !ok {
		return nil
	}
	var out []opportunity.Candidate
	for _, direction := range []market.Direction{market.Buy, market.Sell} {
		dec := s.decide(base.WithDirection(direction))
		if candidate, ok := strategyutil.TechniqueCandidate(ctx, dec, s.spec()); ok {
			out = append(out, candidate)
		}
	}
	return out
}

func (s *Strategy) spec() strategyutil.TechniqueSpec {
	return strategyutil.TechniqueSpec{
		ID: string(ID), Version: Version, ZoneEvidence: "m5_flip_zone_role_confirmed", ConfirmedEvidence: "m5_flip_zone_rejection_confirmed",
		InvalidationLabel: "flip_zone_invalidated", InvalidationBufferATR: s.cfg.InvalidationBufferATR,
		MinimumTargetDistanceATR: s.cfg.MinimumTargetDistanceATR, ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
		Versions: opportunity.AnalysisProvenance{
			StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
			ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
		},
	}
}

// decide mirrors _sd_zone_reaction with require_source="flip_zone" and the key
// level role enforced.
func (s *Strategy) decide(d *strategyutil.LegacyDetector) *strategyutil.TechniqueDecision {
	var zones []techniquezone.Zone
	for _, zone := range d.CandidateZones() {
		if !zone.Mitigated && hasSource(zone, "flip_zone") {
			zones = append(zones, zone)
		}
	}
	zone, _, ok := d.BestValidZone(zones)
	if !ok {
		return nil
	}
	level, found := flipLevel(d.Frame.Levels, zone)
	if !found {
		return nil
	}
	closes := make([]float64, len(d.Frame.Bars))
	for i, bar := range d.Frame.Bars {
		closes[i] = bar.Close
	}
	band := math.Max(level.Band, math.Max(1e-9, d.Settings.ProximalBandATR*math.Max(0, d.ATR)))
	role := keylevel.Role(level.Kind, market.Price(level.Price-band), market.Price(level.Price+band), closes, s.cfg.BreakoutAcceptBars)
	if d.Direction == market.Buy && role != keylevel.RoleBrokenResistance || d.Direction == market.Sell && role != keylevel.RoleBrokenSupport {
		return nil
	}
	conf := d.Reaction(zone.Low(), zone.High(), d.ZoneGrab(zone))
	if conf == nil {
		return nil
	}
	factors := strategyutil.FactorsForConfirmation(strategyutil.ReactionFactors(conf.Type, d.HTFAligned(), zone.Touches), conf.Type)
	low, high := zone.Low(), zone.High()
	result := d.Finish(d.ZoneKey(zone), zone, factors, zone.Side, &low, &high)
	if result == nil {
		return nil
	}
	return &strategyutil.TechniqueDecision{
		Technique: "flip_zone", Direction: d.Direction, Detector: d, Confirmation: conf, Result: result, ID: d.ZoneID(zone),
	}
}

func hasSource(zone techniquezone.Zone, source string) bool {
	if len(zone.Sources) == 0 {
		return zone.Source == source
	}
	for _, s := range zone.Sources {
		if s == source {
			return true
		}
	}
	return false
}

// flipLevel mirrors _flip_zone_level: the key level whose price lies within its
// band of the zone's anchor edge (the bottom of a demand zone, the top of a
// supply zone), the nearest one.
func flipLevel(levels []techniquezone.Level, zone techniquezone.Zone) (techniquezone.Level, bool) {
	anchor := zone.Top
	if zone.Side == "demand" {
		anchor = zone.Bottom
	}
	var best techniquezone.Level
	bestGap, found := 0.0, false
	for _, level := range levels {
		gap := math.Abs(level.Price - anchor)
		if gap > math.Max(level.Band, 0)+1e-9 {
			continue
		}
		if !found || gap < bestGap {
			best, bestGap, found = level, gap, true
		}
	}
	return best, found
}
