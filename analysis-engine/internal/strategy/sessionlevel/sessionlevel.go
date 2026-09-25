// Package sessionlevel implements SessionLevelStrategy — Phase S7
// (apexvoid-bot-prompts/rebuild-strategies.md §26), consuming the
// canonical session-extreme primitive internal/session already builds
// (Asia/London/NY highs-lows, PDH/PDL, PWH/PWL, sweep status). This
// strategy decides tradeability of a not-yet-swept session extreme; the
// primitive already decided what the level IS and whether it has been
// swept.
//
// Thesis: an unswept session extreme, close enough to current price to
// matter, is a standing reaction level — a HIGH level (name suffix _H,
// or PDH/PWH) implies resistance (sell reaction); a LOW level (_L, or
// PDL/PWL) implies support (buy reaction). Like internal/strategy/keylevel,
// this package derives a "current price" proxy from the primary
// timeframe's own most recent Micro-layer swing, since MarketContext
// exposes no raw price feed (see that package's own doc comment for the
// full reasoning — the same limitation applies here).
package sessionlevel

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

const ID strategy.StrategyID = "session_level"
const Version = "v2"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is SessionLevel's own parsed technical configuration.
type Config struct {
	ProximityATR             float64
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
}

// Strategy is SessionLevelStrategy.
type Strategy struct {
	cfg         Config
	timeframe   market.Timeframe
	fingerprint string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("sessionlevel: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("sessionlevel: %w", err)
	}
	return &Strategy{cfg: parsed, timeframe: market.M5, fingerprint: configFingerprint(cfg)}, nil
}

func parseConfig(params map[string]any) (Config, error) {
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
	if proximityATR <= 0 {
		return Config{}, fmt.Errorf("proximity_atr must be > 0")
	}
	if invalidationBuffer <= 0 {
		return Config{}, fmt.Errorf("invalidation_buffer_atr must be > 0")
	}
	if expiryHours <= 0 {
		return Config{}, fmt.Errorf("expiry_hours must be > 0")
	}
	return Config{
		ProximityATR: proximityATR, InvalidationBufferATR: invalidationBuffer,
		MinimumTargetDistanceATR: minimumTargetDistance, ExpiryHours: expiryHours,
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
	if atr <= 0 {
		return nil
	}
	currentPrice, ok := currentPriceProxy(tfCtx.Structure)
	if !ok {
		return nil
	}

	var candidates []opportunity.Candidate
	for _, level := range tfCtx.Session.Levels {
		if level.Swept {
			continue
		}
		direction, isHighLevel := levelDirection(level.Name)
		if !direction.IsValid() {
			continue
		}
		levelPrice := float64(level.Price)
		distanceATR := math.Abs(currentPrice-levelPrice) / atr
		if distanceATR > s.cfg.ProximityATR {
			continue
		}

		var invalidationPrice float64
		var poolSide liquidity.LiquiditySide
		if isHighLevel {
			invalidationPrice = levelPrice + s.cfg.InvalidationBufferATR*atr
			poolSide = liquidity.LiquiditySellSide
		} else {
			invalidationPrice = levelPrice - s.cfg.InvalidationBufferATR*atr
			poolSide = liquidity.LiquidityBuySide
		}

		referencePrice := levelPrice
		targetPool, ok := nearestPool(tfCtx.Liquidity.Pools, poolSide, referencePrice, s.cfg.MinimumTargetDistanceATR*atr, direction)
		if !ok {
			continue
		}
		targetPrice := targetPool.High
		if direction == market.Sell {
			targetPrice = targetPool.Low
		}

		createdAt := currentReferenceTime(tfCtx.Structure)
		expiresAt := createdAt + int64(s.cfg.ExpiryHours*3600)

		setupKey := fmt.Sprintf("session:%s:%d", level.Name, level.Time)
		id, err := opportunity.DeterministicID(opportunity.Identity{
			Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol, Direction: direction, SetupKey: setupKey,
		})
		if err != nil {
			continue
		}

		quality := computeQuality(distanceATR, s.cfg.ProximityATR)

		candidate := opportunity.Candidate{
			ID: id, Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol,
			Direction: direction,
			Entry:     opportunity.EntryZone{Low: levelPrice - atr*0.05, High: levelPrice + atr*0.05},
			Invalidation: market.PriceLevel{
				Price: market.Price(invalidationPrice), Label: "session_level_invalidated",
			},
			Targets: []opportunity.Target{{
				Price: market.PriceLevel{Price: targetPrice, Label: "nearest_opposing_liquidity"},
			}},
			Evidence: []opportunity.Evidence{
				{Code: "session_level_" + strings.ToLower(level.Name)},
				{Code: "session_level_unswept"},
			},
			Quality:   quality,
			CreatedAt: createdAt, ExpiresAt: expiresAt,
			Provenance: opportunity.AnalysisProvenance{
				StructureVersion: structureVersion, LiquidityVersion: liquidityVersion, ZoneVersion: zoneVersionUsed,
				ConfigVersion: configVersion, ConfigFingerprint: s.fingerprint,
			},
		}
		candidates = append(candidates, candidate)
	}
	return candidates
}

// levelDirection reads the reaction direction straight off the level's
// own canonical name — a HIGH-suffixed level (ASIA_H/LONDON_H/NY_H/PDH/
// PWH) implies resistance (sell reaction); a LOW-suffixed level implies
// support (buy reaction). The second return value is isHighLevel, which
// the caller needs separately from Direction to pick the correct
// invalidation side and liquidity pool side — Direction alone conflates
// "valid" with "which side," so a Buy result cannot tell a real LOW
// level apart from an unrecognized name (which never reaches here,
// since Direction("").IsValid() is false). Returns ("", false) for any
// name this strategy does not recognize (fail closed rather than guess).
func levelDirection(name string) (market.Direction, bool) {
	switch {
	case strings.HasSuffix(name, "_H"), name == "PDH", name == "PWH":
		return market.Sell, true // isHighLevel
	case strings.HasSuffix(name, "_L"), name == "PDL", name == "PWL":
		return market.Buy, false // isHighLevel
	default:
		return "", false
	}
}

func currentPriceProxy(structState structure.StructureState) (float64, bool) {
	high, low := structState.Micro.LastHigh, structState.Micro.LastLow
	switch {
	case high != nil && low != nil:
		if high.Time >= low.Time {
			return float64(high.Price), true
		}
		return float64(low.Price), true
	case high != nil:
		return float64(high.Price), true
	case low != nil:
		return float64(low.Price), true
	default:
		return 0, false
	}
}

func currentReferenceTime(structState structure.StructureState) int64 {
	high, low := structState.Micro.LastHigh, structState.Micro.LastLow
	var t int64
	if high != nil && high.Time > t {
		t = high.Time
	}
	if low != nil && low.Time > t {
		t = low.Time
	}
	return t
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

// computeQuality is SessionLevel's own quality model — proximity is its
// only real available dimension (an unswept session level carries no
// touch/strength score the way key-level clustering does), so it uses a
// single, honestly-scoped component rather than fabricating additional
// dimensions with no real signal behind them.
func computeQuality(distanceATR, proximityLimitATR float64) opportunity.StrategyQuality {
	proximityQuality := clamp01(1 - distanceATR/proximityLimitATR)
	return opportunity.StrategyQuality{
		Overall:    proximityQuality,
		Components: map[string]float64{"proximity_quality": proximityQuality},
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
