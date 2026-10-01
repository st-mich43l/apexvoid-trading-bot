// Package keylevel implements KeyLevelStrategy — Phase S7
// (apexvoid-bot-prompts/rebuild-strategies.md §25), consuming the
// canonical key-level clustering primitive internal/keylevel already
// builds (price-clustered swings + round-number levels + wick-touch
// enrichment — this strategy decides tradeability, the primitive already
// decided what a key level IS).
//
// Thesis (ported from the legacy Python detector, app/analysis/
// detectors.py::key_level_reaction, not the simplified proximity-only
// v1 this package originally shipped with — see docs/analysis/
// strategies/key_level.md's "2026-09 port" section): a sufficiently
// touched level is a standing reaction zone only once its role is
// actually classified — support/resistance from an explicit structural
// kind, or (key_levels() only ever emits "reaction"/"round", never an
// explicit kind) a role inferred from price position, with a genuine
// opposing supply/demand zone overlapping the level's own band allowed
// to contradict that naive inference rather than being ignored (see
// opposing.go). A level several consecutive closes have already accepted
// through is reported BROKEN and skipped here — Break & Retest/Trendline
// own that reinterpretation, Key Level must not re-trade it. A bare touch
// is a technical observation, not a trade: only a real, closed-bar
// rejection (confirmation.go) produces a Candidate, and a level where
// BOTH candidate directions independently confirm in the same evaluation
// is a genuine contradiction — discarded entirely, never a coin flip.
//
// internal/keylevel.Level carries no support/resistance field itself
// (that is internal/keylevel.Role's job, ported 1:1 from
// key_level_role.py — see role.go's own doc comment); this package owns
// turning that role, plus real closed-bar price action from
// TimeframeContext.Candles, into a tradeable, confirmed opportunity.
package keylevel

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

const ID strategy.StrategyID = "key_level"
const Version = "v2"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is KeyLevel's own parsed technical configuration.
type Config struct {
	MinimumTouches int
	// MinimumStrength floors keylevel.Level.Strength. The legacy Python
	// detector this package now ports has no equivalent per-level floor
	// (its quality gates live downstream, in confluence/policy) — this
	// stays as an additional, strictly-tighter-never-looser safety floor
	// this platform already had, not a behavior this port removes.
	MinimumStrength float64
	// ProximityATR is how close (in ATR) current price must be to a level
	// for it to be considered at all — likewise an additional pre-filter
	// the legacy detector didn't need (it relied on confirmation alone to
	// bound relevance); kept for the same reason as MinimumStrength.
	ProximityATR             float64
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
	// Reaction is the shared legacy confirmation tuning (see strategyutil).
	Reaction strategyutil.ReactionConfig
	// BreakoutAcceptBars is key_level_role.py's breakout_accept_bars —
	// consecutive closed bars that must accept beyond a level's band
	// before Role reports it BROKEN rather than the level's plain role.
	// Legacy default (TREND_BREAKOUT_ACCEPT_BARS): 2.
	BreakoutAcceptBars int
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
	return &Strategy{cfg: parsed, timeframe: market.M5, fingerprint: configFingerprint(cfg)}, nil
}

func parseConfig(params map[string]any) (Config, error) {
	minimumTouches, err := requireInt(params, "minimum_touches")
	if err != nil {
		return Config{}, err
	}
	minimumStrength, err := requireFloat(params, "minimum_strength")
	if err != nil {
		return Config{}, err
	}
	proximityATR, err := requireFloat(params, "proximity_atr")
	if err != nil {
		return Config{}, err
	}
	invalidationBuffer, err := requireFloat(params, "invalidation_buffer_atr")
	if err != nil {
		return Config{}, err
	}
	minimumTargetDistance, err := requireFloat(params, "minimum_target_distance_atr")
	if err != nil {
		return Config{}, err
	}
	expiryHours, err := requireFloat(params, "expiry_hours")
	if err != nil {
		return Config{}, err
	}
	breakoutAcceptBars, err := requireInt(params, "breakout_accept_bars")
	if err != nil {
		return Config{}, err
	}
	if minimumTouches < 1 {
		return Config{}, fmt.Errorf("minimum_touches must be >= 1")
	}
	if proximityATR <= 0 {
		return Config{}, fmt.Errorf("proximity_atr must be > 0")
	}
	if invalidationBuffer <= 0 {
		return Config{}, fmt.Errorf("invalidation_buffer_atr must be > 0")
	}
	if expiryHours <= 0 {
		return Config{}, fmt.Errorf("expiry_hours must be > 0")
	}
	if breakoutAcceptBars < 1 {
		return Config{}, fmt.Errorf("breakout_accept_bars must be >= 1")
	}
	reactionConfig, err := strategyutil.ParseReactionConfig(params)
	if err != nil {
		return Config{}, err
	}
	return Config{
		MinimumTouches: minimumTouches, MinimumStrength: minimumStrength, ProximityATR: proximityATR,
		InvalidationBufferATR: invalidationBuffer, MinimumTargetDistanceATR: minimumTargetDistance,
		ExpiryHours: expiryHours, BreakoutAcceptBars: breakoutAcceptBars, Reaction: reactionConfig,
	}, nil
}

func requireFloat(params map[string]any, key string) (float64, error) {
	raw, ok := params[key]
	if !ok {
		return 0, fmt.Errorf("missing required parameter %q", key)
	}
	switch v := raw.(type) {
	case float64:
		return v, nil
	case int:
		return float64(v), nil
	default:
		return 0, fmt.Errorf("parameter %q is not numeric (got %T)", key, raw)
	}
}

func requireInt(params map[string]any, key string) (int, error) {
	v, err := requireFloat(params, key)
	if err != nil {
		return 0, err
	}
	return int(v), nil
}

func (s *Strategy) ID() strategy.StrategyID { return ID }

func (s *Strategy) RequiredTimeframes() []market.Timeframe {
	return []market.Timeframe{s.timeframe}
}

func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	tfCtx, ok := ctx.Timeframes[s.timeframe]
	if !ok {
		return nil
	}
	atr := ctx.Volatility.ATR
	if atr <= 0 || len(tfCtx.Candles) == 0 {
		return nil
	}
	currentPrice := tfCtx.Candles[len(tfCtx.Candles)-1].Close
	closes := make([]float64, len(tfCtx.Candles))
	for i, c := range tfCtx.Candles {
		closes[i] = c.Close
	}

	var candidates []opportunity.Candidate
	levels := append([]keylevel.Level(nil), tfCtx.KeyLevel.Levels...)
	sort.Slice(levels, func(i, j int) bool {
		return math.Abs(float64(levels[i].Price)-currentPrice) < math.Abs(float64(levels[j].Price)-currentPrice)
	})
	for _, level := range levels {
		if level.Touches < s.cfg.MinimumTouches || level.Strength < s.cfg.MinimumStrength {
			continue
		}
		levelPrice := float64(level.Price)
		distanceATR := math.Abs(currentPrice-levelPrice) / atr
		if distanceATR > s.cfg.ProximityATR {
			continue
		}

		band := level.Band
		bandLow, bandHigh := market.Price(levelPrice-band), market.Price(levelPrice+band)
		role := keylevel.Role(level.Kind.String(), bandLow, bandHigh, closes, s.cfg.BreakoutAcceptBars)
		if role == keylevel.RoleBrokenSupport || role == keylevel.RoleBrokenResistance {
			continue
		}

		reactLow, reactHigh := bandLow, bandHigh
		var directions []market.Direction
		var contraDirection *market.Direction
		var contraLevel float64
		switch {
		case role == keylevel.RoleSupport:
			directions = []market.Direction{market.Buy}
		case role == keylevel.RoleResistance:
			directions = []market.Direction{market.Sell}
		case currentPrice > float64(bandHigh):
			// No explicit role either way, but price sits above the level —
			// deterministic support hypothesis, unless a real opposing
			// (supply) zone overlapping this band contradicts it.
			if opposing, found := opposingZoneContradicts(tfCtx.Zones.Zones, bandLow, bandHigh, zone.KindDemand); found {
				directions = []market.Direction{market.Buy, market.Sell}
				reactLow, reactHigh = minPrice(bandLow, opposing.Low), maxPrice(bandHigh, opposing.High)
				sell := market.Sell
				contraDirection, contraLevel = &sell, float64(opposing.High)
			} else {
				directions = []market.Direction{market.Buy}
			}
		case currentPrice < float64(bandLow):
			// Level sits above current price — deterministic resistance
			// hypothesis, same caveat mirrored for an opposing demand zone.
			if opposing, found := opposingZoneContradicts(tfCtx.Zones.Zones, bandLow, bandHigh, zone.KindSupply); found {
				directions = []market.Direction{market.Sell, market.Buy}
				reactLow, reactHigh = minPrice(bandLow, opposing.Low), maxPrice(bandHigh, opposing.High)
				buy := market.Buy
				contraDirection, contraLevel = &buy, float64(opposing.Low)
			} else {
				directions = []market.Direction{market.Sell}
			}
		default:
			// Price is inside the level's own band — direction comes from
			// which side actually confirms a reaction, never a guess. If
			// both sides independently confirm, that is a genuine
			// contradiction (handled below), not a coin flip.
			directions = []market.Direction{market.Buy, market.Sell}
		}

		var confirmedHere []opportunity.Candidate
		for _, direction := range directions {
			effectiveLevelPrice := levelPrice
			if contraDirection != nil && direction == *contraDirection {
				effectiveLevelPrice = contraLevel
			}
			reaction := strategyutil.ConfirmReaction(tfCtx, level.ID, direction, float64(reactLow), float64(reactHigh), atr, 0, s.cfg.Reaction)
			if reaction == nil {
				continue
			}
			invalidationBuffer := s.cfg.InvalidationBufferATR * atr
			invalidationPrice := effectiveLevelPrice - band - invalidationBuffer
			poolSide := liquidity.LiquidityBuySide
			if direction == market.Sell {
				invalidationPrice = effectiveLevelPrice + band + invalidationBuffer
				poolSide = liquidity.LiquiditySellSide
			}
			// The entry is the reaction window, which an opposing zone can widen
			// past the level's own band. A stop derived from the level alone then
			// lands inside the entry (published as an invalid event); keep it
			// beyond the outer edge of the whole window.
			if direction == market.Buy {
				invalidationPrice = math.Min(invalidationPrice, float64(reactLow)-invalidationBuffer)
			} else {
				invalidationPrice = math.Max(invalidationPrice, float64(reactHigh)+invalidationBuffer)
			}
			referencePrice := float64(reactHigh)
			if direction == market.Sell {
				referencePrice = float64(reactLow)
			}
			targetPool, ok := nearestPool(tfCtx.Liquidity.Pools, poolSide, referencePrice, s.cfg.MinimumTargetDistanceATR*atr, direction)
			if !ok {
				continue
			}
			targetPrice := targetPool.High
			if direction == market.Sell {
				targetPrice = targetPool.Low
			}

			setupKey := fmt.Sprintf("keylevel:%s:%s:%d:%d", level.ID, direction, reaction.TouchBarTime, reaction.ConfirmationBarTime)
			id, err := opportunity.DeterministicID(opportunity.Identity{
				Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol, Direction: direction, SetupKey: setupKey,
			})
			if err != nil {
				continue
			}

			quality := computeQuality(level, distanceATR, s.cfg.ProximityATR)
			evidence := []opportunity.Evidence{
				{Code: "m5_key_level_" + level.Kind.String()},
				{Code: "m5_key_level_touches_sufficient"},
				{Code: "m5_key_level_role_" + role.String()},
				{Code: "m5_key_level_rejection_confirmed"},
			}
			if contraDirection != nil {
				evidence = append(evidence, opportunity.Evidence{Code: "m5_key_level_opposing_zone_widened"})
			}

			confirmedHere = append(confirmedHere, opportunity.Candidate{
				ID: id, Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol,
				Direction: direction,
				// reaction.ZoneID (the level's own ID), not the local
				// setupKey — that also embeds this reaction's touch/
				// confirmation bar times, which would make two
				// confirmations of the SAME level look like different
				// theses (see Candidate.StructuralID's own doc comment).
				StructuralID: reaction.ZoneID,
				Entry:        opportunity.EntryZone{Low: float64(reactLow), High: float64(reactHigh)},
				Invalidation: market.PriceLevel{
					Price: market.Price(invalidationPrice), Label: "key_level_invalidated",
				},
				Targets: []opportunity.Target{{
					Price: market.PriceLevel{Price: targetPrice, Label: "nearest_opposing_liquidity"},
				}},
				Evidence:  evidence,
				Quality:   quality,
				FormedAt:  level.AnchorTime,
				CreatedAt: reaction.ConfirmationBarTime,
				ExpiresAt: reaction.ConfirmationBarTime + int64(s.cfg.ExpiryHours*3600),
				Provenance: opportunity.AnalysisProvenance{
					StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
					ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
				},
				Reaction: reaction,
			})
		}
		// Zero confirmations: nothing to keep. Two (only reachable when both
		// directions were tried): both sides independently confirmed a
		// reaction off the same level in the same evaluation — a genuine
		// contradiction, not something a quality score should tiebreak.
		// Neither survives; this level produces no opportunity until price
		// action resolves it.
		if len(confirmedHere) == 1 {
			candidates = append(candidates, confirmedHere[0])
		}
	}
	return candidates
}

func minPrice(a, b market.Price) market.Price {
	if a < b {
		return a
	}
	return b
}

func maxPrice(a, b market.Price) market.Price {
	if a > b {
		return a
	}
	return b
}

func nearestPool(pools []liquidity.Pool, side liquidity.LiquiditySide, referencePrice, minimumDistance float64, direction market.Direction) (liquidity.Pool, bool) {
	var matches []liquidity.Pool
	for _, p := range pools {
		if p.Side != side {
			continue
		}
		mid := (float64(p.Low) + float64(p.High)) / 2
		var distance float64
		if direction == market.Buy {
			distance = mid - referencePrice
		} else {
			distance = referencePrice - mid
		}
		if distance < minimumDistance {
			continue
		}
		matches = append(matches, p)
	}
	if len(matches) == 0 {
		return liquidity.Pool{}, false
	}
	sort.Slice(matches, func(i, j int) bool {
		mi := (float64(matches[i].Low) + float64(matches[i].High)) / 2
		mj := (float64(matches[j].Low) + float64(matches[j].High)) / 2
		if direction == market.Buy {
			return mi < mj
		}
		return mi > mj
	})
	return matches[0], true
}

// computeQuality is KeyLevel's own quality model — proximity is its own
// dimension here (source task §40) because, unlike the zone-anchored
// strategies, this primitive has no pre-computed Relevance to reuse.
func computeQuality(level keylevel.Level, distanceATR, proximityLimitATR float64) opportunity.StrategyQuality {
	proximityQuality := clamp01(1 - distanceATR/proximityLimitATR)
	touchQuality := clamp01(float64(level.Touches) / 5.0)
	strengthQuality := clamp01(level.Strength)
	overall := (proximityQuality + touchQuality + strengthQuality) / 3
	return opportunity.StrategyQuality{
		Overall: overall,
		Components: map[string]float64{
			"proximity_quality": proximityQuality,
			"touch_quality":     touchQuality,
			"strength_quality":  strengthQuality,
		},
	}
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

func configFingerprint(cfg strategy.Config) string {
	keys := make([]string, 0, len(cfg.Parameters))
	for k := range cfg.Parameters {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	canonical := string(cfg.ID) + "\x00" + cfg.Version
	for _, k := range keys {
		canonical += "\x00" + k + "=" + fmt.Sprint(cfg.Parameters[k])
	}
	sum := sha256.Sum256([]byte(canonical))
	return hex.EncodeToString(sum[:16])
}
