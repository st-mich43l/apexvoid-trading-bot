// Package demand implements DemandStrategy — Phase S7
// (apexvoid-bot-prompts/rebuild-strategies.md §9/§21), the buy-side sibling
// of internal/strategy/supply, per the Phase S2 catalog's split of the
// legacy "Supply Demand" detector (docs/analysis/strategy-v2-catalog.md,
// row 3). Independently implemented — this package must never import
// internal/strategy/supply, and vice versa
// (test/architecture/dependency_test.go's strategy-isolation rule); any
// resemblance in code shape is a coincidence of two theses that happen to
// be mirror images, not shared strategy behavior.
//
// Thesis: a still-valid (State Fresh/Touched), currently price-relevant
// (Relevance Immediate/Nearby) canonical Demand zone is a standing buy
// reaction zone. Entry is the zone's own band; invalidation is a
// confirmed close beyond the zone's own Low; target is the nearest
// buy-side liquidity pool above.
package demand

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// ID is this strategy's fixed V2 identity.
const ID strategy.StrategyID = "demand"

// Version is this strategy's own technical-thesis version.
const Version = "v2"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is Demand's own parsed technical configuration.
type Config struct {
	MinimumStrength          float64
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
}

// Strategy is DemandStrategy.
type Strategy struct {
	cfg         Config
	rawConfig   strategy.Config
	timeframe   market.Timeframe
	fingerprint string
}

// New constructs DemandStrategy from its registry configuration.
func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("demand: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("demand: %w", err)
	}
	return &Strategy{
		cfg: parsed, rawConfig: cfg, timeframe: market.M5,
		fingerprint: configFingerprint(cfg),
	}, nil
}

func parseConfig(params map[string]any) (Config, error) {
	minimumStrength, err := requireFloat(params, "minimum_strength")
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
	if invalidationBuffer <= 0 {
		return Config{}, fmt.Errorf("invalidation_buffer_atr must be > 0")
	}
	if expiryHours <= 0 {
		return Config{}, fmt.Errorf("expiry_hours must be > 0")
	}
	return Config{
		MinimumStrength: minimumStrength, InvalidationBufferATR: invalidationBuffer,
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

// Evaluate implements strategy.Strategy. Read-only: never mutates ctx.
func (s *Strategy) Evaluate(ctx *context.MarketContext) []opportunity.Candidate {
	tfCtx, ok := ctx.Timeframes[s.timeframe]
	if !ok {
		return nil
	}
	atr := ctx.Volatility.ATR
	if atr <= 0 {
		return nil
	}

	var candidates []opportunity.Candidate
	for _, z := range tfCtx.Zones.Zones {
		if z.Kind != zone.KindDemand {
			continue
		}
		if z.State != zone.StateFresh && z.State != zone.StateTouched {
			continue
		}
		if z.Relevance != zone.Immediate && z.Relevance != zone.Nearby {
			continue
		}
		if z.Strength < s.cfg.MinimumStrength {
			continue
		}

		entryLow, entryHigh := float64(z.Low), float64(z.High)
		invalidationPrice := entryLow - s.cfg.InvalidationBufferATR*atr

		targetPool, ok := nearestPool(tfCtx.Liquidity.Pools, liquidity.LiquidityBuySide, entryHigh, s.cfg.MinimumTargetDistanceATR*atr)
		if !ok {
			continue
		}

		createdAt := zoneReferenceTime(z)
		expiresAt := createdAt + int64(s.cfg.ExpiryHours*3600)

		setupKey := fmt.Sprintf("zone:%s", z.ID)
		id, err := opportunity.DeterministicID(opportunity.Identity{
			Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol, Direction: market.Buy, SetupKey: setupKey,
		})
		if err != nil {
			continue
		}

		quality := computeQuality(z)

		candidate := opportunity.Candidate{
			ID: id, Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol,
			Direction: market.Buy,
			Entry:     opportunity.EntryZone{Low: entryLow, High: entryHigh},
			Invalidation: market.PriceLevel{
				Price: market.Price(invalidationPrice), Label: "demand_zone_invalidated",
			},
			Targets: []opportunity.Target{{
				Price: market.PriceLevel{Price: targetPool.High, Label: "nearest_buy_side_liquidity"},
			}},
			Evidence: []opportunity.Evidence{
				{Code: "m5_demand_zone_" + z.State.String()},
				{Code: "m5_demand_zone_relevance_" + z.Relevance.String()},
				{Code: "m5_demand_zone_layer_" + z.Layer.String()},
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

func nearestPool(pools []liquidity.Pool, side liquidity.LiquiditySide, referencePrice, minimumDistance float64) (liquidity.Pool, bool) {
	var candidates []liquidity.Pool
	for _, p := range pools {
		if p.Side != side {
			continue
		}
		mid := (float64(p.Low) + float64(p.High)) / 2
		distance := mid - referencePrice
		if distance < minimumDistance {
			continue
		}
		candidates = append(candidates, p)
	}
	if len(candidates) == 0 {
		return liquidity.Pool{}, false
	}
	sort.Slice(candidates, func(i, j int) bool {
		mi := (float64(candidates[i].Low) + float64(candidates[i].High)) / 2
		mj := (float64(candidates[j].Low) + float64(candidates[j].High)) / 2
		return mi < mj
	})
	return candidates[0], true
}

func zoneReferenceTime(z zone.Zone) int64 {
	if z.LastTouchedAt != nil && *z.LastTouchedAt > z.CreatedAt {
		return *z.LastTouchedAt
	}
	return z.CreatedAt
}

// computeQuality is Demand's own quality model — independently computed
// from Supply's (source task §40), even though both happen to use
// strength/relevance/freshness as their own dimensions today.
func computeQuality(z zone.Zone) opportunity.StrategyQuality {
	strengthQuality := clamp01(z.Strength)
	relevanceQuality := 0.5
	switch z.Relevance {
	case zone.Immediate:
		relevanceQuality = 1.0
	case zone.Nearby:
		relevanceQuality = 0.6
	}
	touchQuality := 1.0 - 0.15*float64(z.TouchCount)
	if touchQuality < 0.2 {
		touchQuality = 0.2
	}
	overall := (strengthQuality + relevanceQuality + touchQuality) / 3
	return opportunity.StrategyQuality{
		Overall: overall,
		Components: map[string]float64{
			"zone_strength_quality": strengthQuality,
			"relevance_quality":     relevanceQuality,
			"freshness_quality":     touchQuality,
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
