// Package keylevel implements KeyLevelStrategy — Phase S7
// (apexvoid-bot-prompts/rebuild-strategies.md §25), consuming the
// canonical key-level clustering primitive internal/keylevel already
// builds (price-clustered swings + round-number levels + wick-touch
// enrichment — this strategy decides tradeability, the primitive already
// decided what a key level IS).
//
// Thesis: a sufficiently touched, sufficiently strong key level, close
// enough to the current price to matter, is a standing reaction zone —
// support (buy) if price sits above it, resistance (sell) if below.
// Unlike internal/zone, internal/keylevel.Level carries no Relevance
// field (that concept is zone-specific), and MarketContext exposes no
// raw candle/price feed a strategy could read directly — only already-
// computed causal facts (source task §17/§92). This strategy therefore
// derives a "current price" proxy from the primary timeframe's own most
// recently formed Micro-layer swing (structure.LayerState.LastHigh/
// LastLow — whichever is more recent), the smallest and therefore
// closest-to-price structural fact already available, rather than
// re-deriving price from raw OHLC this package cannot see. This is a
// real, documented, non-obvious limitation of the current primitive set,
// not a shortcut: internal/keylevel.Role (the primitive's own live
// support/resistance/broken classifier) needs a real closes series this
// strategy also cannot supply, so it is deliberately not used here
// either — see docs/analysis/strategies/key_level.md.
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
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
	// MinimumStrength floors keylevel.Level.Strength.
	MinimumStrength float64
	// ProximityATR is how close (in ATR) current price must be to a
	// level for it to be a live setup — KeyLevel's own location
	// requirement, independently computed (zone.Relevance's equivalent
	// concept does not exist on keylevel.Level).
	ProximityATR             float64
	InvalidationBufferATR    float64
	MinimumTargetDistanceATR float64
	ExpiryHours              float64
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
	return Config{
		MinimumTouches: minimumTouches, MinimumStrength: minimumStrength, ProximityATR: proximityATR,
		InvalidationBufferATR: invalidationBuffer, MinimumTargetDistanceATR: minimumTargetDistance, ExpiryHours: expiryHours,
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
	if atr <= 0 {
		return nil
	}
	currentPrice, ok := currentPriceProxy(tfCtx.Structure)
	if !ok {
		return nil
	}

	var candidates []opportunity.Candidate
	for _, level := range tfCtx.KeyLevel.Levels {
		if level.Touches < s.cfg.MinimumTouches || level.Strength < s.cfg.MinimumStrength {
			continue
		}
		levelPrice := float64(level.Price)
		distanceATR := math.Abs(currentPrice-levelPrice) / atr
		if distanceATR > s.cfg.ProximityATR {
			continue
		}

		var direction market.Direction
		var invalidationPrice float64
		var poolSide liquidity.LiquiditySide
		if currentPrice >= levelPrice {
			direction = market.Buy // level acting as support
			invalidationPrice = levelPrice - level.Band - s.cfg.InvalidationBufferATR*atr
			poolSide = liquidity.LiquidityBuySide
		} else {
			direction = market.Sell // level acting as resistance
			invalidationPrice = levelPrice + level.Band + s.cfg.InvalidationBufferATR*atr
			poolSide = liquidity.LiquiditySellSide
		}
		entryLow, entryHigh := levelPrice-level.Band, levelPrice+level.Band

		referencePrice := entryHigh
		if direction == market.Sell {
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

		createdAt := currentReferenceTime(tfCtx.Structure)
		expiresAt := createdAt + int64(s.cfg.ExpiryHours*3600)

		// setupKey buckets levelPrice at a fixed FRACTION OF PRICE
		// ITSELF (priceBucketFraction), not of ATR or Band — measured via
		// cmd/replay against real XAU M5 data (Phase S8), levelPrice for
		// the SAME real level is already highly stable across consecutive
		// evaluations (one recurred unchanged across 26 separate closed
		// bars in that run), but both atr and level.Band are ALSO
		// independently recomputed every closed bar, so using either as
		// the bucket STEP let the bucket boundaries themselves drift bar
		// to bar — that made the flooding this bucketing is meant to fix
		// WORSE, not better (147 -> 470 live opportunities for a single
		// strategy over 300 bars), because even a perfectly unchanged
		// levelPrice could round to a different bucket when the step
		// itself moved. A step derived only from levelPrice is
		// self-referential and therefore stable for the same real level.
		// Without any bucketing at all, jitter changes SetupKey (and
		// therefore the deterministic opportunity ID) almost every
		// evaluation, defeating the OpportunityBook's dedup entirely
		// (docs/analysis/opportunity-lifecycle-v2.md; source task §46:
		// "must not publish a new Kafka opportunity on every candle").
		setupKey := fmt.Sprintf("keylevel:%s:%s", bucketPrice(levelPrice, priceBucketStep(levelPrice)), level.Kind.String())
		id, err := opportunity.DeterministicID(opportunity.Identity{
			Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol, Direction: direction, SetupKey: setupKey,
		})
		if err != nil {
			continue
		}

		quality := computeQuality(level, distanceATR, s.cfg.ProximityATR)

		candidate := opportunity.Candidate{
			ID: id, Strategy: ID, StrategyVersion: Version, Symbol: ctx.Symbol,
			Direction: direction,
			Entry:     opportunity.EntryZone{Low: entryLow, High: entryHigh},
			Invalidation: market.PriceLevel{
				Price: market.Price(invalidationPrice), Label: "key_level_invalidated",
			},
			Targets: []opportunity.Target{{
				Price: market.PriceLevel{Price: targetPrice, Label: "nearest_opposing_liquidity"},
			}},
			Evidence: []opportunity.Evidence{
				{Code: "m5_key_level_" + level.Kind.String()},
				{Code: "m5_key_level_touches_sufficient"},
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

// priceBucketFraction is setupKey bucketing's own price-relative
// resolution: 0.05% of price, comfortably finer than any real distinct
// key level's typical separation at this precision while still absorbing
// levelPrice's own small residual across-evaluation jitter. Deliberately
// NOT ATR- or Band-derived — see the call site's doc comment for the
// real, replay-measured reason.
const priceBucketFraction = 0.0005

// priceBucketStep returns setupKey bucketing's step for one levelPrice —
// a fixed fraction of that price itself (never of a live-recomputed
// value like ATR or Band), floored so a near-zero price never produces a
// degenerate zero-width bucket.
func priceBucketStep(levelPrice float64) float64 {
	step := math.Abs(levelPrice) * priceBucketFraction
	if step < 0.0001 {
		step = 0.0001
	}
	return step
}

// bucketPrice rounds price to the nearest multiple of step and formats it
// at fixed precision, so two prices within half a bucket of each other
// produce the identical string — see its call site's doc comment for why
// this matters for setup-key stability. A non-positive step (should not
// occur — keylevel.Level.Band is always a positive cluster half-width in
// practice) falls back to a small fixed epsilon rather than dividing by
// zero.
func bucketPrice(price, step float64) string {
	if step <= 0 {
		step = 0.01
	}
	bucketed := math.Round(price/step) * step
	return fmt.Sprintf("%.6f", bucketed)
}

// currentPriceProxy derives a "current price" reference from the primary
// timeframe's own most recently formed Micro-layer swing — see package
// doc comment for why this substitutes for raw price (unavailable
// through MarketContext).
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
