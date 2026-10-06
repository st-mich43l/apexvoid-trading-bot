// Package keylevel implements Key Level, the strategy that produced the
// profitable XAU week (14–18 Sep 2026: +567 pips, 6W/3L) in the Python era.
//
// It is the frozen Python detector key_level_reaction, run on the same
// detector-contract frame as the other legacy-contract strategies, so the same
// bars give the same level, role, direction, reaction and entry:
//
//  1. Levels are walked nearest first; one with fewer touches than the
//     configured minimum is skipped.
//  2. The level's reaction band is the wider of its own band and the proximal
//     band (proximal_band_atr · ATR). Its closed-bar role (support,
//     resistance, ambiguous, or broken after enough accepted closes) decides
//     the directions tried: support BUY, resistance SELL, and for an ambiguous
//     role the side price sits on, unless a live opposing zone overlaps the
//     band — then both sides are tried over the widened window and the zone's
//     edge is the level the opposite side reacts off. A broken level is
//     Break & Retest's, never reinterpreted here.
//  3. A direction needs a confirmed structural reaction off the band; a level
//     where both sides confirm is a contradiction and yields nothing.
//  4. The reaction passes the shared qualification (level on the right side of
//     price, entry no further than max_entry_atr, confluence at or above the
//     floor).
//  5. Of all levels that qualify, ONE candidate is kept: the highest
//     confluence, nearest level winning ties. The strategy owns that
//     selection; nothing downstream has to choose between its levels.
//
// Key Level carries no strength, proximity or target-room filter of its own:
// the frozen detector had none (its quality gates were confluence and the
// execution policy), and each one measurably removed decisions the profitable
// system took. The candidate's target is the nearest opposing liquidity when
// there is one, else a fixed reward:risk beyond the entry; whether the room is
// enough is the execution policy's decision, as it was.
package keylevel

import (
	"fmt"
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const ID strategy.StrategyID = "key_level"
const Version = "v3"

const (
	contractProfitWeek = "profit_week"
	contractCurrent    = "current"
)

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is Key Level's own parsed technical configuration.
type Config struct {
	MinimumTouches int
	// MinimumSellZoneScore is the optional floor on the nearest supply zone's
	// score for a SELL (0 disables it); the frozen detector carried it as an
	// instrument quality knob.
	MinimumSellZoneScore     float64
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	FallbackTargetR          float64
	ExpiryHours              float64
	PriceDigits              int
	// RequireExplicitRole skips levels whose support/resistance role is still
	// ambiguous (legacy per-instrument Key Level quality rule).
	RequireExplicitRole bool
	// BreakoutAcceptBars is key_level_role.py's breakout_accept_bars.
	BreakoutAcceptBars int
	// Contract selects the structure Key Level reads: "profit_week" is the
	// detector contract of 14–18 Sep 2026 (levels counted from swings, order
	// blocks caused by a BOS), "current" the one the other detectors read.
	Contract string
	Detector strategyutil.LegacyDetectorSettings
}

// Strategy is KeyLevelStrategy.
type Strategy struct {
	cfg         Config
	timeframe   market.Timeframe
	fingerprint string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("keylevel: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("keylevel: %w", err)
	}
	return &Strategy{cfg: parsed, timeframe: market.M5, fingerprint: strategyutil.Fingerprint(string(cfg.ID), cfg.Version, cfg.Parameters)}, nil
}

func parseConfig(params map[string]any) (Config, error) {
	var cfg Config
	var err error
	if cfg.MinimumTouches, err = strategyutil.Int(params, "minimum_touches"); err != nil {
		return Config{}, err
	}
	if cfg.InvalidationBufferATR, err = strategyutil.Float(params, "invalidation_buffer_atr"); err != nil {
		return Config{}, err
	}
	if cfg.MinimumTargetDistanceATR, err = strategyutil.Float(params, "minimum_target_distance_atr"); err != nil {
		return Config{}, err
	}
	if cfg.FallbackTargetR, err = strategyutil.Float(params, "fallback_target_r"); err != nil {
		return Config{}, err
	}
	if cfg.ExpiryHours, err = strategyutil.Float(params, "expiry_hours"); err != nil {
		return Config{}, err
	}
	if cfg.BreakoutAcceptBars, err = strategyutil.Int(params, "breakout_accept_bars"); err != nil {
		return Config{}, err
	}
	if cfg.PriceDigits, err = strategyutil.Int(params, "price_digits"); err != nil {
		return Config{}, err
	}
	contract, ok := params["detector_contract"].(string)
	if !ok || (contract != contractProfitWeek && contract != contractCurrent) {
		return Config{}, fmt.Errorf("detector_contract must be %q or %q", contractProfitWeek, contractCurrent)
	}
	cfg.Contract = contract
	if _, present := params["minimum_sell_zone_score"]; present {
		if cfg.MinimumSellZoneScore, err = strategyutil.Float(params, "minimum_sell_zone_score"); err != nil {
			return Config{}, err
		}
	}
	// Override-only parameter (set from an instrument's overrides, which the
	// config schema cannot express as a base boolean): absent means false.
	if raw, present := params["require_explicit_role"]; present {
		b, ok := raw.(bool)
		if !ok {
			return Config{}, fmt.Errorf("parameter \"require_explicit_role\" is not a boolean (got %T)", raw)
		}
		cfg.RequireExplicitRole = b
	}
	if cfg.Detector, err = strategyutil.ParseLegacyDetectorSettings(params); err != nil {
		return Config{}, err
	}
	switch {
	case cfg.MinimumTouches < 1:
		return Config{}, fmt.Errorf("minimum_touches must be >= 1")
	case cfg.InvalidationBufferATR <= 0:
		return Config{}, fmt.Errorf("invalidation_buffer_atr must be > 0")
	case cfg.FallbackTargetR <= 0:
		return Config{}, fmt.Errorf("fallback_target_r must be > 0")
	case cfg.ExpiryHours <= 0:
		return Config{}, fmt.Errorf("expiry_hours must be > 0")
	case cfg.BreakoutAcceptBars < 1:
		return Config{}, fmt.Errorf("breakout_accept_bars must be >= 1")
	case cfg.PriceDigits < 0:
		return Config{}, fmt.Errorf("price_digits must be >= 0")
	}
	return cfg, nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{s.timeframe}
}

// attempt is one direction's qualified reaction off one level.
type attempt struct {
	direction    market.Direction
	levelPrice   float64
	reactLow     float64
	reactHigh    float64
	confirmation *strategyutil.LegacyConfirmation
	result       *strategyutil.LegacyResult
	detector     *strategyutil.LegacyDetector
	level        techniquezone.Level
	role         keylevel.RoleKind
	widened      bool
}

func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	base, ok := strategyutil.NewLegacyDetectorForFrame(ctx, s.timeframe, s.cfg.Detector)
	if !ok {
		return nil
	}
	frame, price, atr := base.Frame, base.Price, base.ATR
	closes := make([]float64, len(frame.Bars))
	for i, bar := range frame.Bars {
		closes[i] = bar.Close
	}
	zoneBandFloor := math.Max(1e-9, s.cfg.Detector.ProximalBandATR*math.Max(0, atr))
	levels, zones := s.structure(base)

	var best *attempt
	for _, level := range levels {
		if level.Touches < maxInt(1, s.cfg.MinimumTouches) {
			continue
		}
		band := math.Max(level.Band, zoneBandFloor)
		bandLow, bandHigh := level.Price-band, level.Price+band
		role := keylevel.Role(level.Kind, market.Price(bandLow), market.Price(bandHigh), closes, s.cfg.BreakoutAcceptBars)
		// An accepted role flip belongs to Break & Retest; Key Level never
		// reinterprets it in the opposite direction.
		if role == keylevel.RoleBrokenSupport || role == keylevel.RoleBrokenResistance {
			continue
		}
		if role == keylevel.RoleAmbiguous && s.cfg.RequireExplicitRole {
			continue
		}

		reactLow, reactHigh := bandLow, bandHigh
		var directions []market.Direction
		var contra market.Direction
		var contraLevel float64
		widened := false
		switch {
		case role == keylevel.RoleSupport:
			directions = []market.Direction{market.Buy}
		case role == keylevel.RoleResistance:
			directions = []market.Direction{market.Sell}
		case price > bandHigh:
			if opposing, found := opposingZone(zones, bandLow, bandHigh, "supply"); found {
				directions = []market.Direction{market.Buy, market.Sell}
				reactLow, reactHigh = math.Min(bandLow, opposing.Low()), math.Max(bandHigh, opposing.High())
				contra, contraLevel, widened = market.Sell, opposing.High(), true
			} else {
				directions = []market.Direction{market.Buy}
			}
		case price < bandLow:
			if opposing, found := opposingZone(zones, bandLow, bandHigh, "demand"); found {
				directions = []market.Direction{market.Sell, market.Buy}
				reactLow, reactHigh = math.Min(bandLow, opposing.Low()), math.Max(bandHigh, opposing.High())
				contra, contraLevel, widened = market.Buy, opposing.Low(), true
			} else {
				directions = []market.Direction{market.Sell}
			}
		default:
			// Price is inside the level's own band: the direction comes from which
			// side actually confirms, never a guess.
			directions = []market.Direction{market.Buy, market.Sell}
		}

		var confirmed []attempt
		for _, direction := range directions {
			if direction == market.Sell && s.cfg.MinimumSellZoneScore > 0 {
				score, found := nearestSameSideZoneScore(zones, price, "supply")
				if !found || score < s.cfg.MinimumSellZoneScore {
					continue
				}
			}
			levelPrice := level.Price
			if contra != "" && direction == contra {
				levelPrice = contraLevel
			}
			d := base.WithDirection(direction)
			conf := d.Reaction(reactLow, reactHigh, d.LevelGrab(level.Price, level.Band))
			if conf == nil {
				continue
			}
			zoneSide := "demand"
			if direction == market.Sell {
				zoneSide = "supply"
			}
			zone := techniquezone.Zone{Bottom: reactLow, Top: reactHigh, Side: zoneSide, Source: "level", BreakIndex: -1}
			factors := strategyutil.FactorsForConfirmation(reactionFactors(conf.Type, d.HTFAligned(), level.Touches), conf.Type)
			result := d.Finish(levelPrice, zone, factors, level.Kind, &reactLow, &reactHigh)
			if result == nil {
				continue
			}
			confirmed = append(confirmed, attempt{
				direction: direction, levelPrice: levelPrice, reactLow: reactLow, reactHigh: reactHigh,
				confirmation: conf, result: result, detector: d, level: level, role: role, widened: widened,
			})
		}
		// Zero confirmations: nothing to keep. Two (both sides confirmed off the
		// same level in one evaluation): a contradiction, not a coin flip.
		if len(confirmed) != 1 {
			continue
		}
		if best == nil || confirmed[0].result.Stars > best.result.Stars {
			chosen := confirmed[0]
			best = &chosen
		}
	}
	if best == nil {
		return nil
	}
	candidate, ok := s.candidate(ctx, base, best)
	if !ok {
		return nil
	}
	return []opportunity.Candidate{candidate}
}

// reactionFactors mirrors _reaction_factors: the evidence the confirmation
// actually showed, mapped onto the common rubric. The session context is
// always on, as the frozen scanner never narrowed it.
func reactionFactors(confirmationType string, htfAligned bool, touches int) confluence.Factors {
	return confluence.Factors{
		HTFAligned:          htfAligned,
		Touches:             touches,
		WickRejection:       confirmationType == reaction.TypeWickRejection,
		DisplacementGrade:   confirmationType == reaction.TypeEngulfing,
		StructuralAgreement: confirmationType == reaction.TypeSweepReclaim || confirmationType == reaction.TypeStrongReclaim,
		SessionContext:      true,
	}
}

func (s *Strategy) candidate(ctx *context.MarketContext, d *strategyutil.LegacyDetector, a *attempt) (opportunity.Candidate, bool) {
	atr := d.ATR
	direction := a.direction
	buffer := s.cfg.InvalidationBufferATR * atr
	low, high := a.reactLow, a.reactHigh
	invalidation, reference := low-buffer, high
	if direction == market.Sell {
		invalidation, reference = high+buffer, low
	}
	target, targetLabel, ok := s.target(d, direction, reference, invalidation, atr)
	if !ok {
		return opportunity.Candidate{}, false
	}
	levelID := fmt.Sprintf("keylevel:%s:%.*f", a.level.Kind, s.cfg.PriceDigits, a.level.Price)
	conf := a.confirmation
	setupKey := fmt.Sprintf("%s:%s:%d:%d", levelID, direction, conf.TouchTime, conf.ConfirmationTime)
	evidence := []string{
		"m5_key_level_" + a.level.Kind,
		"m5_key_level_touches_sufficient",
		"m5_key_level_role_" + a.role.String(),
		"m5_key_level_rejection_confirmed",
	}
	if a.widened {
		evidence = append(evidence, "m5_key_level_opposing_zone_widened")
	}
	stars := float64(a.result.Stars)
	quality := opportunity.StrategyQuality{
		Overall: strategyutil.Clamp01(stars / 3),
		Components: map[string]float64{
			"confluence":    strategyutil.Clamp01(stars / 3),
			"touch_quality": strategyutil.Clamp01(float64(a.level.Touches) / 5),
			"reaction":      1,
		},
	}
	candidate, err := strategyutil.Candidate(strategyutil.CandidateSpec{
		ID: string(ID), Version: Version, SetupKey: setupKey, Symbol: ctx.Symbol, Direction: direction,
		EntryLow: low, EntryHigh: high, Invalidation: invalidation, InvalidationLabel: "key_level_invalidated",
		Target: target, TargetLabel: targetLabel, Evidence: evidence, Quality: quality,
		FormedAt: conf.TouchTime, ConfirmedAt: conf.ConfirmationTime, ExpiryHours: s.cfg.ExpiryHours, Fingerprint: s.fingerprint,
	})
	if err != nil {
		return opportunity.Candidate{}, false
	}
	// The persistent identity of the thesis is the level, not this
	// confirmation: a re-confirmation of the same level is the same thesis.
	candidate.StructuralID = levelID
	candidate.Reaction = &opportunity.ReactionConfirmation{
		ZoneID: levelID, TouchBarTime: conf.TouchTime, ConfirmationBarTime: conf.ConfirmationTime,
		ReactionType: "rejection", Pattern: conf.Type,
	}
	candidate.DetectorConfluence = a.result.ConfluenceContext()
	candidate.Provenance = opportunity.AnalysisProvenance{
		StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
		ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
	}
	return candidate, true
}

// target is the nearest unswept opposing liquidity at least the configured
// distance from the entry; without one, a fixed reward:risk beyond it. Whether
// the room is enough is the execution policy's decision, not a filter here.
func (s *Strategy) target(d *strategyutil.LegacyDetector, direction market.Direction, reference, invalidation, atr float64) (float64, string, bool) {
	want := "buy"
	if direction == market.Sell {
		want = "sell"
	}
	minimum := s.cfg.MinimumTargetDistanceATR * atr
	best, bestDistance := 0.0, math.Inf(1)
	for _, pool := range d.Frame.Pools {
		if pool.Side != want {
			continue
		}
		distance := pool.Level - reference
		if direction == market.Sell {
			distance = reference - pool.Level
		}
		if distance >= minimum && distance < bestDistance {
			best, bestDistance = pool.Level, distance
		}
	}
	if !math.IsInf(bestDistance, 1) {
		return best, "nearest_opposing_liquidity", true
	}
	risk := math.Abs(reference - invalidation)
	if risk <= 0 {
		return 0, "", false
	}
	if direction == market.Sell {
		return reference - s.cfg.FallbackTargetR*risk, "fixed_reward_risk", true
	}
	return reference + s.cfg.FallbackTargetR*risk, "fixed_reward_risk", true
}

// structure returns the levels (nearest first) and zones Key Level reads under
// its configured detector contract.
func (s *Strategy) structure(d *strategyutil.LegacyDetector) ([]techniquezone.Level, []techniquezone.Zone) {
	levels, zones := d.Frame.Levels, d.Frame.Zones
	if s.cfg.Contract == contractProfitWeek {
		levels, zones = d.Frame.SwingLevels, d.Frame.ContractZones
	}
	ordered := append([]techniquezone.Level(nil), levels...)
	sort.SliceStable(ordered, func(i, j int) bool {
		return math.Abs(ordered[i].Price-d.Price) < math.Abs(ordered[j].Price-d.Price)
	})
	return ordered, zones
}

// opposingZone mirrors _opposing_zone_contradicts: the first live zone of the
// given side overlapping the band. (The frozen detector scanned the scored
// zones and then their order-block view, which repeats members of the first.)
func opposingZone(zones []techniquezone.Zone, bandLow, bandHigh float64, side string) (techniquezone.Zone, bool) {
	for _, z := range zones {
		if z.Side != side || z.Mitigated {
			continue
		}
		if z.Low() <= bandHigh && z.High() >= bandLow {
			return z, true
		}
	}
	return techniquezone.Zone{}, false
}

// nearestSameSideZoneScore mirrors _nearest_same_side_zone_score: the score of
// the same-side zone containing price, else the one whose midpoint is nearest.
func nearestSameSideZoneScore(zones []techniquezone.Zone, price float64, side string) (float64, bool) {
	var best *techniquezone.Zone
	bestDistance := math.Inf(1)
	for i := range zones {
		z := zones[i]
		if z.Side != side {
			continue
		}
		distance := 0.0
		if !(z.Low() <= price && price <= z.High()) {
			distance = math.Abs((z.Low()+z.High())/2 - price)
		}
		if best == nil || distance < bestDistance {
			zone := z
			best, bestDistance = &zone, distance
		}
	}
	if best == nil {
		return 0, false
	}
	return best.Score, true
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
