package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// This file is deliberately the ONLY place in analysis-engine that reads
// config.Document and turns it into structure.Settings/liquidity.Config/
// history depths/the canonical ATR selection. structure, liquidity, and
// marketdata all take plain Go values, never a *config.Document — engine
// is where config (rank 1) and the domain packages it wires together
// (ranks 2-3) both become reachable at once (docs/architecture/
// dependency-rules.md).

// ATRSettings selects the one canonical ATR algorithm/length — source
// task §27: "there must be one authoritative ATR... the chosen algorithm
// must be read from config."
type ATRSettings struct {
	Algorithm indicator.Algorithm
	Length    int
}

// ATRSettingsFromConfig reads analysis.indicators.atr.{algorithm,length}.
func ATRSettingsFromConfig(doc *config.Document) (ATRSettings, error) {
	algo, err := getString(doc, "analysis.indicators.atr.algorithm")
	if err != nil {
		return ATRSettings{}, err
	}
	length, err := getInt(doc, "analysis.indicators.atr.length")
	if err != nil {
		return ATRSettings{}, err
	}
	return ATRSettings{Algorithm: indicator.Algorithm(algo), Length: length}, nil
}

// HistoryDepthsFromConfig reads analysis.history.depth.* into the
// map[Timeframe]int internal/marketdata.NewMarketHistory needs — source
// task §5/§69.
func HistoryDepthsFromConfig(doc *config.Document) (map[market.Timeframe]int, error) {
	section, err := doc.Section("analysis.history.depth")
	if err != nil {
		return nil, err
	}
	depths := make(map[market.Timeframe]int, len(section))
	for key := range section {
		tf, err := market.ParseTimeframe(key)
		if err != nil {
			return nil, fmt.Errorf("engine: analysis.history.depth.%s: %w", key, err)
		}
		depth, err := getInt(doc, "analysis.history.depth."+key)
		if err != nil {
			return nil, err
		}
		depths[tf] = depth
	}
	return depths, nil
}

// StructureSettingsFromConfig reads analysis.structure.* into
// structure.Settings.
func StructureSettingsFromConfig(doc *config.Document) (structure.Settings, error) {
	version, err := getString(doc, "analysis.structure.version")
	if err != nil {
		return structure.Settings{}, err
	}
	if version != "v2" {
		return structure.Settings{}, fmt.Errorf(
			"engine: unsupported analysis.structure.version %q — only \"v2\" is implemented, fail closed per source task §58/§67", version,
		)
	}
	leftBars, err := getInt(doc, "analysis.structure.pivot.left_bars")
	if err != nil {
		return structure.Settings{}, err
	}
	rightBars, err := getInt(doc, "analysis.structure.pivot.right_bars")
	if err != nil {
		return structure.Settings{}, err
	}
	minExcursion, err := getFloat(doc, "analysis.structure.swing.minimum_excursion_atr")
	if err != nil {
		return structure.Settings{}, err
	}
	internalATR, err := getFloat(doc, "analysis.structure.swing.promotion.internal_atr")
	if err != nil {
		return structure.Settings{}, err
	}
	intermediateATR, err := getFloat(doc, "analysis.structure.swing.promotion.intermediate_atr")
	if err != nil {
		return structure.Settings{}, err
	}
	majorATR, err := getFloat(doc, "analysis.structure.swing.promotion.major_atr")
	if err != nil {
		return structure.Settings{}, err
	}
	equalTolerance, err := getFloat(doc, "analysis.structure.equal_level.tolerance_atr")
	if err != nil {
		return structure.Settings{}, err
	}
	minPenetration, err := getFloat(doc, "analysis.structure.break.minimum_penetration_atr")
	if err != nil {
		return structure.Settings{}, err
	}
	dispRangeATR, err := getFloat(doc, "analysis.structure.break.displacement_range_atr")
	if err != nil {
		return structure.Settings{}, err
	}
	dispBodyDom, err := getFloat(doc, "analysis.structure.break.displacement_body_dominance")
	if err != nil {
		return structure.Settings{}, err
	}
	sweepReclaimBars, err := getInt(doc, "analysis.structure.break.sweep_reclaim_bars")
	if err != nil {
		return structure.Settings{}, err
	}
	failedBreakReclaimBars, err := getInt(doc, "analysis.structure.break.failed_break_reclaim_bars")
	if err != nil {
		return structure.Settings{}, err
	}

	return structure.Settings{
		Version:        version,
		PivotLeftBars:  leftBars,
		PivotRightBars: rightBars,
		Promotion: structure.PromotionConfig{
			MinimumExcursionATR: minExcursion,
			InternalATR:         internalATR,
			IntermediateATR:     intermediateATR,
			MajorATR:            majorATR,
		},
		EqualToleranceATR: equalTolerance,
		Break: structure.BreakConfig{
			MinimumPenetrationATR:  minPenetration,
			SweepReclaimBars:       sweepReclaimBars,
			FailedBreakReclaimBars: failedBreakReclaimBars,
			DisplacementMaxBars:    sweepReclaimBars, // scan the same horizon a break itself would wait out for a reclaim — one coherent time window, not a second independent one
			Displacement: structure.DisplacementConfig{
				RangeATR:      dispRangeATR,
				BodyDominance: dispBodyDom,
			},
		},
	}, nil
}

// LiquidityConfigFromConfig reads analysis.liquidity.* into
// liquidity.Config.
func LiquidityConfigFromConfig(doc *config.Document) (liquidity.Config, error) {
	version, err := getString(doc, "analysis.liquidity.version")
	if err != nil {
		return liquidity.Config{}, err
	}
	if version != "v1" {
		return liquidity.Config{}, fmt.Errorf(
			"engine: unsupported analysis.liquidity.version %q — only \"v1\" is implemented, fail closed per source task §58/§67", version,
		)
	}
	tolerance, err := getFloat(doc, "analysis.liquidity.equal_level_tolerance_atr")
	if err != nil {
		return liquidity.Config{}, err
	}
	minTouches, err := getInt(doc, "analysis.liquidity.pool_minimum_touches")
	if err != nil {
		return liquidity.Config{}, err
	}
	sweepReclaimBars, err := getInt(doc, "analysis.liquidity.sweep_reclaim_bars")
	if err != nil {
		return liquidity.Config{}, err
	}
	return liquidity.Config{
		Version: version, EqualLevelToleranceATR: tolerance,
		PoolMinimumTouches: minTouches, SweepReclaimBars: sweepReclaimBars,
	}, nil
}

func getFloat(doc *config.Document, path string) (float64, error) {
	raw, ok := doc.Get(path)
	if !ok {
		return 0, fmt.Errorf("engine: missing required config %q", path)
	}
	switch v := raw.(type) {
	case float64:
		return v, nil
	case int:
		return float64(v), nil
	default:
		return 0, fmt.Errorf("engine: config %q is not a number (got %T)", path, raw)
	}
}

func getInt(doc *config.Document, path string) (int, error) {
	raw, ok := doc.Get(path)
	if !ok {
		return 0, fmt.Errorf("engine: missing required config %q", path)
	}
	switch v := raw.(type) {
	case int:
		return v, nil
	case float64:
		return int(v), nil
	default:
		return 0, fmt.Errorf("engine: config %q is not an integer (got %T)", path, raw)
	}
}

func getInt64(doc *config.Document, path string) (int64, error) {
	v, err := getInt(doc, path)
	return int64(v), err
}

func getString(doc *config.Document, path string) (string, error) {
	raw, ok := doc.Get(path)
	if !ok {
		return "", fmt.Errorf("engine: missing required config %q", path)
	}
	s, ok := raw.(string)
	if !ok {
		return "", fmt.Errorf("engine: config %q is not a string (got %T)", path, raw)
	}
	return s, nil
}

func getBool(doc *config.Document, path string) (bool, error) {
	raw, ok := doc.Get(path)
	if !ok {
		return false, fmt.Errorf("engine: missing required config %q", path)
	}
	b, ok := raw.(bool)
	if !ok {
		return false, fmt.Errorf("engine: config %q is not a boolean (got %T)", path, raw)
	}
	return b, nil
}

// KafkaConfigFromConfig reads transport.kafka.* into kafka.Config — the
// Kafka transport task's own addition to this file, following the exact
// same discipline as every function above it (Kafka transport task).
// internal/transport/kafka itself never reads config.Document; this is
// the one and only place that translates YAML into kafka.Config.
func KafkaConfigFromConfig(doc *config.Document) (kafka.Config, error) {
	enabled, err := getBool(doc, "transport.kafka.enabled")
	if err != nil {
		return kafka.Config{}, err
	}
	brokersRaw, ok := doc.Get("transport.kafka.brokers")
	if !ok {
		return kafka.Config{}, fmt.Errorf("engine: missing required config %q", "transport.kafka.brokers")
	}
	brokersList, ok := brokersRaw.([]any)
	if !ok {
		return kafka.Config{}, fmt.Errorf("engine: config %q is not a list (got %T)", "transport.kafka.brokers", brokersRaw)
	}
	brokers := make([]string, 0, len(brokersList))
	for i, b := range brokersList {
		s, ok := b.(string)
		if !ok {
			return kafka.Config{}, fmt.Errorf("engine: transport.kafka.brokers[%d] is not a string (got %T)", i, b)
		}
		brokers = append(brokers, s)
	}
	clientID, err := getString(doc, "transport.kafka.client_id.analysis_engine")
	if err != nil {
		return kafka.Config{}, err
	}
	consumerGroup, err := getString(doc, "transport.kafka.consumer_groups.analysis_engine")
	if err != nil {
		return kafka.Config{}, err
	}
	tickEnabled, err := getBool(doc, "transport.kafka.tick_consumption_enabled")
	if err != nil {
		return kafka.Config{}, err
	}
	marketBarClosed, err := getString(doc, "transport.kafka.topics.market_bar_closed")
	if err != nil {
		return kafka.Config{}, err
	}
	marketTick, err := getString(doc, "transport.kafka.topics.market_tick")
	if err != nil {
		return kafka.Config{}, err
	}
	analysisOpportunity, err := getString(doc, "transport.kafka.topics.analysis_opportunity")
	if err != nil {
		return kafka.Config{}, err
	}
	analysisOpportunityInvalidated, err := getString(doc, "transport.kafka.topics.analysis_opportunity_invalidated")
	if err != nil {
		return kafka.Config{}, err
	}
	topicSpecs, err := kafkaTopicSpecsFromConfig(doc)
	if err != nil {
		return kafka.Config{}, err
	}

	cfg := kafka.Config{
		Enabled: enabled, Brokers: brokers, ClientID: clientID,
		Topics: kafka.Topics{
			MarketBarClosed: marketBarClosed, MarketTick: marketTick,
			AnalysisOpportunity: analysisOpportunity, AnalysisOpportunityInvalidated: analysisOpportunityInvalidated,
		},
		TopicSpecs:             topicSpecs,
		ConsumerGroup:          consumerGroup,
		TickConsumptionEnabled: tickEnabled,
	}
	if err := cfg.Validate(); err != nil {
		return kafka.Config{}, err
	}
	return cfg, nil
}

func kafkaTopicSpecsFromConfig(doc *config.Document) (map[string]kafka.TopicSpec, error) {
	raw, ok := doc.Get("transport.kafka.topic_specs")
	if !ok {
		return nil, fmt.Errorf("engine: missing required config %q", "transport.kafka.topic_specs")
	}
	entries, ok := raw.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("engine: config %q is not a mapping (got %T)", "transport.kafka.topic_specs", raw)
	}
	result := make(map[string]kafka.TopicSpec, len(entries))
	for name, value := range entries {
		mapping, ok := value.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("engine: config transport.kafka.topic_specs.%s is not a mapping (got %T)", name, value)
		}
		partitions, err := topicSpecInt(mapping, "partitions", name)
		if err != nil {
			return nil, err
		}
		replication, err := topicSpecInt(mapping, "replication_factor", name)
		if err != nil {
			return nil, err
		}
		retention, err := topicSpecInt(mapping, "retention_ms", name)
		if err != nil {
			return nil, err
		}
		result[name] = kafka.TopicSpec{Partitions: int32(partitions), Replication: int16(replication), RetentionMillis: int64(retention)}
	}
	return result, nil
}

func topicSpecInt(mapping map[string]any, key, topic string) (int, error) {
	value, ok := mapping[key]
	if !ok {
		return 0, fmt.Errorf("engine: missing required config transport.kafka.topic_specs.%s.%s", topic, key)
	}
	switch typed := value.(type) {
	case int:
		return typed, nil
	case int64:
		return int(typed), nil
	case float64:
		return int(typed), nil
	default:
		return 0, fmt.Errorf("engine: config transport.kafka.topic_specs.%s.%s is not an integer (got %T)", topic, key, value)
	}
}

// ConfigProvenanceFromConfig computes kafka.ConfigProvenance from the
// resolved document — source task §13: "which exact ApexVoid
// configuration generated this opportunity?" answered historically.
// config_version is Configuration V3's own "version: 3" document marker
// (doc.Get("version"), already required by internal/config.ResolveDocument
// itself); config_fingerprint is a SHA-256 of the resolved document's own
// canonical JSON form (Go's encoding/json sorts map[string]any keys
// alphabetically, so this is deterministic without extra
// canonicalization work) — a hash, never the configuration content
// itself, so it can never leak a secret (source task §13: "never
// include configuration secrets"). Computed once at startup by the
// composition root, never per event.
func ConfigProvenanceFromConfig(doc *config.Document) (kafka.ConfigProvenance, error) {
	versionRaw, ok := doc.Get("version")
	if !ok {
		return kafka.ConfigProvenance{}, fmt.Errorf("engine: resolved document missing its own %q marker", "version")
	}
	version, ok := versionRaw.(int)
	if !ok {
		return kafka.ConfigProvenance{}, fmt.Errorf("engine: %q is not an integer (got %T)", "version", versionRaw)
	}
	raw, err := json.Marshal(doc.Raw())
	if err != nil {
		return kafka.ConfigProvenance{}, fmt.Errorf("engine: computing config fingerprint: %w", err)
	}
	sum := sha256.Sum256(raw)
	return kafka.ConfigProvenance{Version: version, Fingerprint: hex.EncodeToString(sum[:16])}, nil
}
