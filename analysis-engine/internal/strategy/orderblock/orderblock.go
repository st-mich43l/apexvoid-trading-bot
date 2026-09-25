// Package orderblock implements OrderBlockStrategy — Phase S7
// (apexvoid-bot-prompts/rebuild-strategies.md §20), consuming the
// canonical Order Block primitive internal/zone already builds (origin
// candle/displacement/structure-break relationship — not "last bearish
// candle before bullish move" alone, per that primitive's own doc
// comment). This strategy decides tradeability; the primitive already
// decided what an order block IS.
//
// Thesis: a still-valid, currently-relevant order block zone is a
// standing reaction zone in whichever direction its own Side implies
// (zone.Side.Direction() — Demand-side OB expects a bounce/buy,
// Supply-side OB expects a rejection/sell). Entry is the zone's own
// band; invalidation is a confirmed close through the block's far edge;
// target is the nearest liquidity pool on the trade side.
package orderblock

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

const ID strategy.StrategyID = "order_block"
const Version = "v2"

const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3
)

// Config is OrderBlock's own parsed technical configuration.
type Config struct {
	MinimumStrength          float64
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
}

// Strategy is OrderBlockStrategy.
type Strategy struct {
	cfg         Config
	timeframe   market.Timeframe
	fingerprint string
}

func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("orderblock: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("orderblock: %w", err)
	}
	return &Strategy{cfg: parsed, timeframe: market.M5, fingerprint: configFingerprint(cfg)}, nil
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
		if z.Kind != zone.KindOrderBlock {
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

		direction := z.Side.Direction()
		entryLow, entryHigh := float64(z.Low), float64(z.High)

		var invalidationPrice float64
		var poolSide liquidity.LiquiditySide
		var referencePrice float64
		if direction == market.Buy {
			invalidationPrice = entryLow - s.cfg.InvalidationBufferATR*atr
			poolSide = liquidity.LiquidityBuySide
			referencePrice = entryHigh
		} else {
			invalidationPrice = entryHigh + s.cfg.InvalidationBufferATR*atr
			poolSide = liquidity.LiquiditySellSide
			referencePrice = entryLow
		}

		targetPool, ok := nearestPool(tfCtx.Liquidity.Pools, poolSide, referencePrice, s.cfg.MinimumTargetDistanceATR*atr, direction)
		if !ok {
			continue
		}
		targetPrice := targetPool.High
		if direction == market.Sell {
			targetPrice = targetPool.Low
		}

		createdAt := zoneReferenceTime(z)
		expiresAt := createdAt + int64(s.cfg.ExpiryHours*3600)

		setupKey := fmt.Sprintf("zone:%s", z.ID)
		id, err := opportunity.DeterministicID(opportunity.Identity{
			Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol, Direction: direction, SetupKey: setupKey,
		})
		if err != nil {
			continue
		}

		quality := computeQuality(z)

		candidate := opportunity.Candidate{
			ID: id, Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol,
			Direction: direction,
			Entry:     opportunity.EntryZone{Low: entryLow, High: entryHigh},
			Invalidation: market.PriceLevel{
				Price: market.Price(invalidationPrice), Label: "order_block_invalidated",
			},
			Targets: []opportunity.Target{{
				Price: market.PriceLevel{Price: targetPrice, Label: "nearest_opposing_liquidity"},
			}},
			Evidence: []opportunity.Evidence{
				{Code: "m5_order_block_" + z.State.String()},
				{Code: "m5_order_block_relevance_" + z.Relevance.String()},
				{Code: "m5_order_block_side_" + z.Side.String()},
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

func zoneReferenceTime(z zone.Zone) int64 {
	if z.LastTouchedAt != nil && *z.LastTouchedAt > z.CreatedAt {
		return *z.LastTouchedAt
	}
	return z.CreatedAt
}

// computeQuality is OrderBlock's own quality model — independently
// computed, even though the strength/relevance/freshness shape recurs
// across the zone-anchored strategies in this catalog slice (source task
// §40: recurring dimension names are not shared strategy behavior).
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
