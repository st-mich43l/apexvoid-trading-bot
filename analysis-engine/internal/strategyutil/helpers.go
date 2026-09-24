// Package strategyutil contains mechanics shared by independent strategy
// packages. It deliberately contains no setup detector or universal scoring
// formula; each strategy owns those decisions.
package strategyutil

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"sort"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

func Float(params map[string]any, key string) (float64, error) {
	raw, ok := params[key]
	if !ok {
		return 0, fmt.Errorf("missing required parameter %q", key)
	}
	switch value := raw.(type) {
	case float64:
		return value, nil
	case int:
		return float64(value), nil
	default:
		return 0, fmt.Errorf("parameter %q is not numeric (got %T)", key, raw)
	}
}

func Int(params map[string]any, key string) (int, error) {
	value, err := Float(params, key)
	if err != nil {
		return 0, err
	}
	if value != math.Trunc(value) {
		return 0, fmt.Errorf("parameter %q must be an integer", key)
	}
	return int(value), nil
}

func Fingerprint(id, version string, params map[string]any) string {
	keys := make([]string, 0, len(params))
	for key := range params {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	canonical := id + "\x00" + version
	for _, key := range keys {
		canonical += "\x00" + key + "=" + fmt.Sprint(params[key])
	}
	sum := sha256.Sum256([]byte(canonical))
	return hex.EncodeToString(sum[:16])
}

func LastBar(ctx *analysiscontext.MarketContext, tf market.Timeframe) (market.Candle, bool) {
	timeframe, ok := ctx.Timeframes[tf]
	if !ok || len(timeframe.Candles) == 0 {
		return market.Candle{}, false
	}
	return timeframe.Candles[len(timeframe.Candles)-1], true
}

func Clamp01(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 1 {
		return 1
	}
	return value
}

// OpposingLiquidity selects the nearest unswept objective in the trade
// direction. Strategy packages decide whether this objective is suitable and
// may use a different target thesis instead.
func OpposingLiquidity(pools []liquidity.Pool, direction market.Direction, from, minimumDistance float64) (market.Price, bool) {
	want := liquidity.LiquidityBuySide
	if direction == market.Sell {
		want = liquidity.LiquiditySellSide
	}
	bestDistance := math.Inf(1)
	var best market.Price
	for _, pool := range pools {
		if pool.Side != want || pool.SweptAt != nil {
			continue
		}
		price := float64(pool.High)
		if direction == market.Sell {
			price = float64(pool.Low)
		}
		distance := price - from
		if direction == market.Sell {
			distance = from - price
		}
		if distance >= minimumDistance && distance < bestDistance {
			bestDistance, best = distance, market.Price(price)
		}
	}
	return best, !math.IsInf(bestDistance, 1)
}

type CandidateSpec struct {
	ID, Version, SetupKey string
	Symbol                market.Symbol
	Direction             market.Direction
	EntryLow, EntryHigh   float64
	Invalidation          float64
	InvalidationLabel     string
	Target                float64
	TargetLabel           string
	Evidence              []string
	Quality               opportunity.StrategyQuality
	FormedAt, ConfirmedAt int64
	ExpiryHours           float64
	Fingerprint           string
}

func Candidate(spec CandidateSpec) (opportunity.Candidate, error) {
	id, err := opportunity.DeterministicID(opportunity.Identity{
		Strategy: opportunity.StrategyID(spec.ID), StrategyVersion: spec.Version,
		Symbol: spec.Symbol, Direction: spec.Direction, SetupKey: spec.SetupKey,
	})
	if err != nil {
		return opportunity.Candidate{}, err
	}
	evidence := make([]opportunity.Evidence, len(spec.Evidence))
	for i, code := range spec.Evidence {
		evidence[i] = opportunity.Evidence{Code: code}
	}
	candidate := opportunity.Candidate{
		ID: id, Strategy: opportunity.StrategyID(spec.ID), StrategyVersion: spec.Version,
		Symbol: spec.Symbol, Direction: spec.Direction,
		Entry:        opportunity.EntryZone{Low: spec.EntryLow, High: spec.EntryHigh},
		Invalidation: market.PriceLevel{Price: market.Price(spec.Invalidation), Label: spec.InvalidationLabel},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: market.Price(spec.Target), Label: spec.TargetLabel}}},
		Evidence:     evidence, Quality: spec.Quality,
		FormedAt: spec.FormedAt, CreatedAt: spec.ConfirmedAt,
		ExpiresAt: spec.ConfirmedAt + int64(spec.ExpiryHours*3600),
		Provenance: opportunity.AnalysisProvenance{
			StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1",
			ConfigVersion: 3, ConfigFingerprint: spec.Fingerprint,
		},
	}
	// Timeframes are fed independently. A higher-timeframe formation can be
	// newer than a lagging lower-timeframe confirmation during reconnect or
	// historical backfill. Such a cross-timeframe snapshot is not causal, so
	// suppress it until a later evaluation has aligned bars rather than leaking
	// an invalid candidate to the registry/publisher.
	if err := candidate.Validate(); err != nil {
		return opportunity.Candidate{}, err
	}
	return candidate, nil
}
