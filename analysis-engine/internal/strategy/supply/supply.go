// Package supply implements SupplyStrategy — Phase S7
// (apexvoid-bot-prompts/rebuild-strategies.md §9/§21), one of the two
// strategies the Phase S2 catalog split the legacy "Supply Demand"
// detector into (docs/analysis/strategy-v2-catalog.md, row 3). Supply and
// demand share a canonical primitive (internal/zone's displacement-based
// Supply/Demand geometry) but are independently-owned strategies here —
// this package must never be imported by internal/strategy/demand or vice
// versa (test/architecture/dependency_test.go's strategy-isolation rule).
//
// Thesis: a still-valid (State Fresh/Touched, never Mitigated/Invalidated),
// currently price-relevant (Relevance Immediate/Nearby) canonical Supply
// zone is a standing sell reaction zone. Entry is the zone's own band —
// this is a resting/limit thesis, not a "price just touched it this bar"
// reaction: MarketContext exposes only already-computed causal facts
// (zone.Relevance is itself recomputed from the real last close inside
// zone.Update — source task §17), never raw OHLC, so "is this zone
// currently relevant" is answered by the canonical primitive, not
// re-derived here from candle wicks.
package supply

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

// ID is this strategy's fixed V2 identity (docs/analysis/strategy-v2-catalog.md).
const ID strategy.StrategyID = "supply"

// Version is this strategy's own technical-thesis version — increments
// only on a behavior-breaking redesign (source task §13), independent of
// structure/liquidity/zone's own algorithm versions below.
const Version = "v2"

// Algorithm versions this strategy's logic was validated against — a
// real, version-pinned contract fact each strategy owns (matches
// internal/engine/config.go's own "only vN is supported" fail-closed
// pattern), not tuning.
const (
	structureVersion = "v2"
	liquidityVersion = "v1"
	zoneVersionUsed  = "v1"
	configVersion    = 3 // Configuration V3's own fixed version number.
)

// Config is Supply's own parsed technical configuration — parsed from
// strategy.Config.Parameters, never a generic registry-supplied shape
// (source task §51: "each concrete factory... must parse and validate
// its technical thresholds itself").
type Config struct {
	// MinimumStrength floors zone.Zone.Strength — a weak/marginal supply
	// zone is not a tradeable thesis.
	MinimumStrength float64
	// InvalidationBufferATR is how far beyond the zone's own High a
	// confirmed close must reach before the thesis is invalidated —
	// never zero (a bare touch of the zone's own edge is not itself
	// invalidation).
	InvalidationBufferATR float64
	// MinimumTargetDistanceATR rejects a target pool too close to entry
	// to represent a meaningful technical objective.
	MinimumTargetDistanceATR float64
	// ExpiryHours is this strategy's own technical expiry window (source
	// task's opportunity-lifecycle SETUP_EXPIRED reason) — distinct from
	// Algo Bot's execution-age policy (docs/analysis/opportunity-lifecycle-v2.md).
	ExpiryHours float64
}

// Strategy is SupplyStrategy — see package doc comment for its thesis.
type Strategy struct {
	cfg         Config
	rawConfig   strategy.Config
	timeframe   market.Timeframe
	fingerprint string
}

// New constructs SupplyStrategy from its registry configuration. Fails
// closed on a missing/invalid required parameter (source task §56: "no
// hidden strategy values... fail startup on missing required strategy
// config").
func New(cfg strategy.Config) (strategy.Strategy, error) {
	if cfg.ID != ID {
		return nil, fmt.Errorf("supply: New called with strategy ID %q, want %q", cfg.ID, ID)
	}
	parsed, err := parseConfig(cfg.Parameters)
	if err != nil {
		return nil, fmt.Errorf("supply: %w", err)
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

// Evaluate implements strategy.Strategy. Read-only: never mutates ctx
// (source task §92).
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
		if z.Kind != zone.KindSupply {
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
		invalidationPrice := entryHigh + s.cfg.InvalidationBufferATR*atr

		targetPool, ok := nearestPool(tfCtx.Liquidity.Pools, liquidity.LiquiditySellSide, entryLow, s.cfg.MinimumTargetDistanceATR*atr)
		if !ok {
			continue
		}

		createdAt := zoneReferenceTime(z)
		expiresAt := createdAt + int64(s.cfg.ExpiryHours*3600)

		setupKey := fmt.Sprintf("zone:%s", z.ID)
		id, err := opportunity.DeterministicID(opportunity.Identity{
			Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol, Direction: market.Sell, SetupKey: setupKey,
		})
		if err != nil {
			continue
		}

		quality := computeQuality(z)

		candidate := opportunity.Candidate{
			ID: id, Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol,
			Direction: market.Sell,
			Entry:     opportunity.EntryZone{Low: entryLow, High: entryHigh},
			Invalidation: market.PriceLevel{
				Price: market.Price(invalidationPrice), Label: "supply_zone_invalidated",
			},
			Targets: []opportunity.Target{{
				Price: market.PriceLevel{Price: market.Price(targetPool.Low), Label: "nearest_sell_side_liquidity"},
			}},
			Evidence: []opportunity.Evidence{
				{Code: "m5_supply_zone_" + z.State.String()},
				{Code: "m5_supply_zone_relevance_" + z.Relevance.String()},
				{Code: "m5_supply_zone_layer_" + z.Layer.String()},
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

// nearestPool returns the liquidity pool of side, on the correct side of
// referencePrice, at least minimumDistance away, closest to
// referencePrice — the technical target objective (source task §48:
// "opposing liquidity... never account-based TP sizing").
func nearestPool(pools []liquidity.Pool, side liquidity.LiquiditySide, referencePrice, minimumDistance float64) (liquidity.Pool, bool) {
	var candidates []liquidity.Pool
	for _, p := range pools {
		if p.Side != side {
			continue
		}
		mid := (float64(p.Low) + float64(p.High)) / 2
		distance := referencePrice - mid
		if side == liquidity.LiquidityBuySide {
			distance = mid - referencePrice
		}
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
		if side == liquidity.LiquidityBuySide {
			return mi < mj
		}
		return mi > mj
	})
	return candidates[0], true
}

// zoneReferenceTime is the most recent real timestamp this zone carries —
// used as this Candidate's CreatedAt. MarketContext exposes no
// evaluation-time clock (strategy.Strategy.Evaluate takes only ctx), so
// the most recent already-known causal fact about the zone itself is the
// correct, deterministic, replay-safe substitute — never the SetupKey
// (docs/analysis/opportunity-lifecycle-v2.md: "must not be an evaluation
// timestamp").
func zoneReferenceTime(z zone.Zone) int64 {
	if z.LastTouchedAt != nil && *z.LastTouchedAt > z.CreatedAt {
		return *z.LastTouchedAt
	}
	return z.CreatedAt
}

// computeQuality is Supply's own quality model (source task §39) —
// dimensions specific to a displacement-origin reaction zone. Not shared
// with Demand/OrderBlock/etc., even though the shape of "some components
// plus an overall score" recurs — each strategy computes its own values
// independently (source task §40).
func computeQuality(z zone.Zone) opportunity.StrategyQuality {
	strengthQuality := clamp01(z.Strength)
	relevanceQuality := 0.5
	switch z.Relevance {
	case zone.Immediate:
		relevanceQuality = 1.0
	case zone.Nearby:
		relevanceQuality = 0.6
	}
	// A fresh zone (never touched) is the cleanest read; each additional
	// touch erodes confidence the zone still holds, down to a floor.
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

// configFingerprint hashes THIS strategy's own registry configuration
// (id/version/sorted parameters) — a real, deterministic value, though
// deliberately not the whole resolved Configuration V3 document's own
// fingerprint: a strategy has no access to *config.Document (it sits
// below internal/config's rank; only internal/engine reads it). Phase S8
// (engine wiring, not this phase) is expected to enrich/overwrite
// Provenance.ConfigFingerprint with the real whole-document fingerprint
// before a Candidate reaches the OpportunityBook/Kafka — this value is a
// legitimate bootstrapping placeholder, not a permanent design choice,
// and is documented as such rather than silently presented as final.
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
