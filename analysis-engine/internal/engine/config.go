package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/arbitration"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/barrier"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/legacyread"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/mad"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
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

// RegimeConfigFromConfig reads the shared regime/breakout contract. Regime
// thresholds are analysis facts and therefore belong in the composition root,
// not as hidden constants inside a strategy package.
func RegimeConfigFromConfig(doc *config.Document) (regime.Config, error) {
	filterEnabled, err := getBool(doc, "analysis.regime.chop.filter_enabled")
	if err != nil {
		return regime.Config{}, err
	}
	lookback, err := getInt(doc, "analysis.regime.chop.lookback")
	if err != nil {
		return regime.Config{}, err
	}
	rangeATR, err := getFloat(doc, "analysis.regime.chop.range_atr")
	if err != nil {
		return regime.Config{}, err
	}
	coil, err := getFloat(doc, "analysis.coil_contract")
	if err != nil {
		return regime.Config{}, err
	}
	direction, err := getBool(doc, "analysis.regime.direction_enabled")
	if err != nil {
		return regime.Config{}, err
	}
	directionLookback, err := getInt(doc, "analysis.regime.direction_lookback")
	if err != nil {
		return regime.Config{}, err
	}
	minSwings, err := getInt(doc, "analysis.regime.min_directional_swings")
	if err != nil {
		return regime.Config{}, err
	}
	minDisplacement, err := getFloat(doc, "analysis.regime.min_displacement_atr")
	if err != nil {
		return regime.Config{}, err
	}
	equalTolerance, err := getFloat(doc, "analysis.structure.equal_level.tolerance_atr")
	if err != nil {
		return regime.Config{}, err
	}
	buffer, err := getFloat(doc, "analysis.breakout.buffer_atr")
	if err != nil {
		return regime.Config{}, err
	}
	acceptBars, err := getInt(doc, "analysis.breakout.accept_bars")
	if err != nil {
		return regime.Config{}, err
	}
	return regime.Config{
		ChopFilterEnabled: filterEnabled, ChopLookback: lookback, ChopRangeATR: rangeATR,
		CoilContract: coil, DirectionEnabled: direction, DirectionLookback: directionLookback,
		MinDirectionalSwings: minSwings, MinDisplacementATR: minDisplacement,
		EqualToleranceATR: equalTolerance,
		BreakoutBufferATR: buffer, BreakoutAcceptBars: acceptBars,
	}, nil
}

// MADConfigFromConfig reads the shared Asia-phase analysis contract. MAD is a
// technical context input; it is intentionally not read from execution.*.
func MADConfigFromConfig(doc *config.Document) (mad.Config, error) {
	readFloat := func(path string) (float64, error) { return getFloat(doc, path) }
	minRQ, err := readFloat("analysis.mad.accum.minimum_rq")
	if err != nil {
		return mad.Config{}, err
	}
	maxRQ, err := readFloat("analysis.mad.accum.maximum_rq")
	if err != nil {
		return mad.Config{}, err
	}
	breakATR, err := readFloat("analysis.mad.expand.break_atr")
	if err != nil {
		return mad.Config{}, err
	}
	displacementATR, err := readFloat("analysis.mad.expand.displacement_atr")
	if err != nil {
		return mad.Config{}, err
	}
	acceptCloses, err := getInt(doc, "analysis.mad.expand.accept_closes")
	if err != nil {
		return mad.Config{}, err
	}
	minPen, err := readFloat("analysis.mad.manip.min_penetration_atr")
	if err != nil {
		return mad.Config{}, err
	}
	minReclaim, err := readFloat("analysis.mad.manip.min_reclaim_atr")
	if err != nil {
		return mad.Config{}, err
	}
	pipSize, err := readFloat("analysis.mad.pip_size")
	if err != nil {
		return mad.Config{}, err
	}
	asiaStart, err := getInt(doc, "analysis.sessions.asia_start")
	if err != nil {
		return mad.Config{}, err
	}
	londonStart, err := getInt(doc, "analysis.sessions.london_start")
	if err != nil {
		return mad.Config{}, err
	}
	return mad.Config{AsiaStartHour: asiaStart, LondonStartHour: londonStart, AccumMinimumRQ: minRQ, AccumMaximumRQ: maxRQ, ExpandBreakATR: breakATR, ExpandDisplacementATR: displacementATR, ExpandAcceptCloses: acceptCloses, ManipMinimumPenetrationATR: minPen, ManipMinimumReclaimATR: minReclaim, PipSize: pipSize}, nil
}

// ConfluenceConfigFromConfig reads the versioned technical confluence scorer.
// These values used to exist only in Python's DetectorSettings; keeping the
// lookup here makes Go the single live technical owner while preserving the
// same canonical YAML paths for the Python compatibility projection.
func ConfluenceConfigFromConfig(doc *config.Document) (confluence.Config, error) {
	version, err := getString(doc, "analysis.confluence.scoring_version")
	if err != nil {
		return confluence.Config{}, err
	}
	three, err := getFloat(doc, "analysis.confluence.v2_star_three_ratio")
	if err != nil {
		return confluence.Config{}, err
	}
	two, err := getFloat(doc, "analysis.confluence.v2_star_two_ratio")
	if err != nil {
		return confluence.Config{}, err
	}
	zoneWeight, err := getFloat(doc, "analysis.confluence.v2_zone_quality_weight")
	if err != nil {
		return confluence.Config{}, err
	}
	madWeight, err := getFloat(doc, "analysis.confluence.v2_mad_score_weight")
	if err != nil {
		return confluence.Config{}, err
	}
	fibWeight, err := getFloat(doc, "analysis.fibonacci.confluence_weight")
	if err != nil {
		return confluence.Config{}, err
	}
	if version != "v1" && version != "v2" {
		return confluence.Config{}, fmt.Errorf("engine: unsupported confluence scoring version %q", version)
	}
	if !(two >= 0 && two < three && three <= 1 && zoneWeight >= 0 && madWeight >= 0 && fibWeight >= 0) {
		return confluence.Config{}, fmt.Errorf("engine: invalid confluence scorer thresholds or weights")
	}
	return confluence.Config{
		ScoringVersion: version, StarThreeRatio: three, StarTwoRatio: two,
		ZoneQualityWeight: zoneWeight, MADScoreWeight: madWeight,
		FibonacciWeight: fibWeight,
	}, nil
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

// ZoneConfigFromConfig reads the canonical Zone leaves into zone.Config,
// reusing analysis.structure.break.displacement_range_atr/
// displacement_body_dominance directly for Displacement — one canonical
// displacement formula shared with structure, never a second copy (see
// internal/zone/doc.go).
func ZoneConfigFromConfig(doc *config.Document) (zone.Config, error) {
	version, err := getString(doc, "analysis.zones.version")
	if err != nil {
		return zone.Config{}, err
	}
	if version != "v1" {
		return zone.Config{}, fmt.Errorf(
			"engine: unsupported analysis.zones.version %q — only \"v1\" is implemented, fail closed per source task §58/§67", version,
		)
	}
	dispRangeATR, err := getFloat(doc, "analysis.structure.break.displacement_range_atr")
	if err != nil {
		return zone.Config{}, err
	}
	dispBodyDom, err := getFloat(doc, "analysis.structure.break.displacement_body_dominance")
	if err != nil {
		return zone.Config{}, err
	}
	invalidationToleranceATR, err := getFloat(doc, "analysis.techniques.invalidation_tolerance_atr")
	if err != nil {
		return zone.Config{}, err
	}
	sweepReclaimBars, err := getInt(doc, "analysis.techniques.sweep_reclaim_bars")
	if err != nil {
		return zone.Config{}, err
	}
	maxBreakEpisodes, err := getInt(doc, "analysis.techniques.max_break_episodes")
	if err != nil {
		return zone.Config{}, err
	}
	retestMaxTouches, err := getInt(doc, "analysis.techniques.retest_max_touches")
	if err != nil {
		return zone.Config{}, err
	}
	epsilonATR, err := getFloat(doc, "analysis.structure.equal_level.tolerance_atr")
	if err != nil {
		return zone.Config{}, err
	}
	immediateATR, err := getFloat(doc, "analysis.zone_relevance.immediate_atr")
	if err != nil {
		return zone.Config{}, err
	}
	nearbyATR, err := getFloat(doc, "analysis.zone_relevance.nearby_atr")
	if err != nil {
		return zone.Config{}, err
	}
	remoteATR, err := getFloat(doc, "analysis.zone_relevance.remote_atr")
	if err != nil {
		return zone.Config{}, err
	}
	if !(immediateATR < nearbyATR && nearbyATR < remoteATR) {
		return zone.Config{}, fmt.Errorf(
			"engine: analysis.zone_relevance must satisfy immediate_atr < nearby_atr < remote_atr, got %v < %v < %v",
			immediateATR, nearbyATR, remoteATR,
		)
	}
	flipAcceptBars, err := getInt(doc, "analysis.flip_zone.accept_bars")
	if err != nil {
		return zone.Config{}, err
	}
	flipBandBodyFraction, err := getFloat(doc, "analysis.flip_zone.band_body_fraction")
	if err != nil {
		return zone.Config{}, err
	}
	flipLevelBandATR, err := getFloat(doc, "analysis.structure.equal_level.tolerance_atr")
	if err != nil {
		return zone.Config{}, err
	}
	orderBlockBodyFraction := dispBodyDom

	return zone.Config{
		Version: version,
		Displacement: structure.DisplacementConfig{
			RangeATR:      dispRangeATR,
			BodyDominance: dispBodyDom,
		},
		Lifecycle: zone.LifecycleConfig{
			InvalidationToleranceATR: invalidationToleranceATR,
			SweepReclaimBars:         sweepReclaimBars,
			MaxBreakEpisodes:         maxBreakEpisodes,
			RetestMaxTouches:         retestMaxTouches,
			EpsilonATR:               epsilonATR,
		},
		Relevance: zone.RelevanceConfig{
			ImmediateATR: immediateATR,
			NearbyATR:    nearbyATR,
			RemoteATR:    remoteATR,
		},
		FlipAcceptBars:         flipAcceptBars,
		FlipBandBodyFraction:   flipBandBodyFraction,
		FlipLevelBandATR:       flipLevelBandATR,
		OrderBlockBodyFraction: orderBlockBodyFraction,
	}, nil
}

// TrendlineConfigFromConfig reads analysis.trendlines.* into
// trendline.Config. Fails closed on any
// version other than "v2": V1 (trendlines.py::_trendlines_v1) is
// confirmed dead/shadow-metrics-only in production and is not ported
// (see internal/trendline/doc.go).
func TrendlineConfigFromConfig(doc *config.Document) (trendline.Config, error) {
	version, err := getString(doc, "analysis.trendlines.version")
	if err != nil {
		return trendline.Config{}, err
	}
	if version != "v2" {
		return trendline.Config{}, fmt.Errorf(
			"engine: unsupported analysis.trendlines.version %q — only \"v2\" is implemented, fail closed per source task §58/§67", version,
		)
	}
	minimumSlopeATR, err := getFloat(doc, "analysis.trendlines.minimum_slope_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	maximumSlopeATR, err := getFloat(doc, "analysis.trendlines.maximum_slope_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	minimumTouchSpacingBars, err := getInt(doc, "analysis.trendlines.minimum_touch_spacing_bars")
	if err != nil {
		return trendline.Config{}, err
	}
	minimumSpanBars, err := getInt(doc, "analysis.trendlines.minimum_span_bars")
	if err != nil {
		return trendline.Config{}, err
	}
	minimumValidationTouchSpacingBars, err := getInt(doc, "analysis.trendlines.minimum_validation_touch_spacing_bars")
	if err != nil {
		return trendline.Config{}, err
	}
	validationTouchToleranceATR, err := getFloat(doc, "analysis.trendlines.validation_touch_tolerance_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	invalidationPenetrationATR, err := getFloat(doc, "analysis.trendlines.invalidation_penetration_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	closeViolationATR, err := getFloat(doc, "analysis.trendlines.close_violation_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	approachMinDistanceATR, err := getFloat(doc, "analysis.trendlines.approach_min_distance_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	minimumValidationFavorableExcursionATR, err := getFloat(doc, "analysis.trendlines.minimum_validation_favorable_excursion_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	validationReactionBars, err := getInt(doc, "analysis.trendlines.validation_reaction_bars")
	if err != nil {
		return trendline.Config{}, err
	}
	minimumValidationTouches, err := getInt(doc, "analysis.trendlines.minimum_validation_touches")
	if err != nil {
		return trendline.Config{}, err
	}
	exhaustionValidationTouches, err := getInt(doc, "analysis.trendlines.exhaustion_validation_touches")
	if err != nil {
		return trendline.Config{}, err
	}
	maximumWickViolations, err := getInt(doc, "analysis.trendlines.maximum_wick_violations")
	if err != nil {
		return trendline.Config{}, err
	}
	dedupValueATR, err := getFloat(doc, "analysis.trendlines.dedup_value_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	dedupSlopePercent, err := getFloat(doc, "analysis.trendlines.dedup_slope_percent")
	if err != nil {
		return trendline.Config{}, err
	}
	interactionBandATR, err := getFloat(doc, "analysis.trendlines.interaction_band_atr")
	if err != nil {
		return trendline.Config{}, err
	}
	return trendline.Config{
		MinimumSlopeATR:                        minimumSlopeATR,
		MaximumSlopeATR:                        maximumSlopeATR,
		MinimumTouchSpacingBars:                minimumTouchSpacingBars,
		MinimumSpanBars:                        minimumSpanBars,
		MinimumValidationTouchSpacingBars:      minimumValidationTouchSpacingBars,
		ValidationTouchToleranceATR:            validationTouchToleranceATR,
		InvalidationPenetrationATR:             invalidationPenetrationATR,
		CloseViolationATR:                      closeViolationATR,
		ApproachMinDistanceATR:                 approachMinDistanceATR,
		MinimumValidationFavorableExcursionATR: minimumValidationFavorableExcursionATR,
		ValidationReactionBars:                 validationReactionBars,
		MinimumValidationTouches:               minimumValidationTouches,
		ExhaustionValidationTouches:            exhaustionValidationTouches,
		MaximumWickViolations:                  maximumWickViolations,
		DedupValueATR:                          dedupValueATR,
		DedupSlopePercent:                      dedupSlopePercent,
		InteractionBandATR:                     interactionBandATR,
	}, nil
}

// KeyLevelConfigFromConfig reads analysis.key_levels.* into
// keylevel.Config. No version gate: levels.py
// has never had a versioned contract.
func KeyLevelConfigFromConfig(doc *config.Document) (keylevel.Config, error) {
	clusterATR, err := getFloat(doc, "analysis.key_levels.cluster_atr")
	if err != nil {
		return keylevel.Config{}, err
	}
	roundStep, err := getFloat(doc, "analysis.key_levels.round_step")
	if err != nil {
		return keylevel.Config{}, err
	}
	minimumTouches, err := getInt(doc, "analysis.key_levels.minimum_touches")
	if err != nil {
		return keylevel.Config{}, err
	}
	maximumClusterSpanMultiple, err := getFloat(doc, "analysis.key_levels.maximum_cluster_span_multiple")
	if err != nil {
		return keylevel.Config{}, err
	}
	return keylevel.Config{
		ClusterATR:                 clusterATR,
		RoundStep:                  roundStep,
		MinimumTouches:             minimumTouches,
		MaximumClusterSpanMultiple: maximumClusterSpanMultiple,
	}, nil
}

// SessionConfigFromConfig reads analysis.sessions.* into session.Config —
// Session configuration. asia/london/ny_start are the same leaves
// StructureConfigFromConfig's siblings already read for scanner/session
// display purposes; daily_rollover_utc_hour is the one leaf this phase
// adds (see config/analysis.yml).
func SessionConfigFromConfig(doc *config.Document) (session.Config, error) {
	asiaStart, err := getInt(doc, "analysis.sessions.asia_start")
	if err != nil {
		return session.Config{}, err
	}
	londonStart, err := getInt(doc, "analysis.sessions.london_start")
	if err != nil {
		return session.Config{}, err
	}
	nyStart, err := getInt(doc, "analysis.sessions.ny_start")
	if err != nil {
		return session.Config{}, err
	}
	rolloverHour, err := getInt(doc, "analysis.sessions.daily_rollover_utc_hour")
	if err != nil {
		return session.Config{}, err
	}
	return session.Config{
		AsiaStartHour:        asiaStart,
		LondonStartHour:      londonStart,
		NYStartHour:          nyStart,
		DailyRolloverUTCHour: rolloverHour,
	}, nil
}

// FibConfigFromConfig reads analysis.fibonacci.* into fib.Config —
// Fibonacci configuration. No version gate (unlike Zone/Structure):
// fibonacci.py/dealing_range.py have never had a versioned contract, and
// neither leaf here changes shape between versions.
func FibConfigFromConfig(doc *config.Document) (fib.Config, error) {
	epsilonATR, err := getFloat(doc, "analysis.fibonacci.epsilon_atr")
	if err != nil {
		return fib.Config{}, err
	}
	deepDiscount, err := getFloat(doc, "analysis.fibonacci.deep_discount")
	if err != nil {
		return fib.Config{}, err
	}
	deepPremium, err := getFloat(doc, "analysis.fibonacci.deep_premium")
	if err != nil {
		return fib.Config{}, err
	}
	eqHalfBand, err := getFloat(doc, "analysis.fibonacci.eq_half_band")
	if err != nil {
		return fib.Config{}, err
	}
	return fib.Config{
		EpsilonATR:   epsilonATR,
		DeepDiscount: deepDiscount,
		DeepPremium:  deepPremium,
		EqHalfBand:   eqHalfBand,
	}, nil
}

// ArbitrationConfigFromConfig reads analysis.arbitration.* into
// arbitration.Config.
func ArbitrationConfigFromConfig(doc *config.Document) (arbitration.Config, error) {
	conflictMarginQuality, err := getFloat(doc, "analysis.arbitration.conflict_margin_quality")
	if err != nil {
		return arbitration.Config{}, err
	}
	inPlayATR, err := getFloat(doc, "analysis.arbitration.in_play_atr")
	if err != nil {
		return arbitration.Config{}, err
	}
	return arbitration.Config{ConflictMarginQuality: conflictMarginQuality, InPlayATR: inPlayATR}, nil
}

// StopEnvelopeConfig is the per-strategy-family stop-distance risk policy
// (Phase 4) — the SAME execution.reaction/range/trend leaves algo-bot's
// own protective_stop.py reads, and execution.trend.stop_max_pips is
// already shared_with_ctrader (MismatchPolicy.FATAL) — reading it here
// too extends an existing cross-service contract, not a new one.
type StopEnvelopeConfig struct {
	ReactionMinRR         float64
	ReactionMinPips       float64
	ReactionMaxPips       float64
	ReactionRoomFloorPips float64
	RangeMinRR            float64
	RangeRoomFloorPips    float64
	ScalpMinPips          float64
	ScalpMaxPips          float64
	TrendMinPips          float64
	TrendMaxPips          float64
	// InstrumentMinPips/InstrumentMaxPips are populated by ApplyInstrument
	// from instruments.yml.  They override the global family envelope for
	// non-M1-scalp candidates; the latter retain strategies.scalping.stop.
	InstrumentMinPips    float64
	InstrumentMaxPips    float64
	InstrumentConfigured bool
	// InstrumentScalp* is the instrument's own band for the range-room
	// scalps (Range Edge, Fade Scalp), when it declares one.
	InstrumentScalpMinPips    float64
	InstrumentScalpMaxPips    float64
	InstrumentScalpConfigured bool
}

// StopEnvelopeConfigFromConfig reads execution.{reaction,range,trend,stops}.*
// and auto_algo.strategies.scalping.stop.* — the one place YAML becomes
// StopEnvelopeConfig, mirroring every other *ConfigFromConfig function in
// this file.
func StopEnvelopeConfigFromConfig(doc *config.Document) (StopEnvelopeConfig, error) {
	reactionMinRR, err := getFloat(doc, "execution.reaction.room_stop_min_rr")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	reactionMinPips, err := getFloat(doc, "execution.reaction.stop_min_pips")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	reactionMaxPips, err := getFloat(doc, "execution.reaction.stop_max_pips")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	reactionRoomFloorPips, err := getFloat(doc, "execution.stops.reaction.room_floor_pips")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	rangeMinRR, err := getFloat(doc, "execution.range.min_rr")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	rangeRoomFloorPips, err := getFloat(doc, "execution.range.room_stop_floor_pips")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	scalpMinPips, err := getFloat(doc, "auto_algo.strategies.scalping.stop.minimum_pips")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	scalpMaxPips, err := getFloat(doc, "auto_algo.strategies.scalping.stop.maximum_pips")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	trendMinPips, err := getFloat(doc, "execution.stops.trend.minimum_pips")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	trendMaxPips, err := getFloat(doc, "execution.trend.stop_max_pips")
	if err != nil {
		return StopEnvelopeConfig{}, err
	}
	return StopEnvelopeConfig{
		ReactionMinRR: reactionMinRR, ReactionMinPips: reactionMinPips, ReactionMaxPips: reactionMaxPips,
		ReactionRoomFloorPips: reactionRoomFloorPips, RangeMinRR: rangeMinRR, RangeRoomFloorPips: rangeRoomFloorPips,
		ScalpMinPips: scalpMinPips, ScalpMaxPips: scalpMaxPips, TrendMinPips: trendMinPips, TrendMaxPips: trendMaxPips,
	}, nil
}

// StrategyConfigsFromConfig reads the complete semantic V2 strategy catalog
// from analysis.strategies. This is intentionally only registry-level
// configuration: each concrete strategy owns validation of its
// technical parameters. Unknown and omitted IDs fail at startup so a strategy
// is never silently enabled, disabled, or defaulted by an incidental YAML
// typo.
func StrategyConfigsFromConfig(doc *config.Document) ([]strategy.Config, error) {
	section, err := doc.Section("analysis.strategies")
	if err != nil {
		return nil, err
	}
	known := make(map[string]struct{}, len(strategy.KnownIDs()))
	for _, id := range strategy.KnownIDs() {
		known[string(id)] = struct{}{}
	}
	for name := range section {
		if _, ok := known[name]; !ok {
			return nil, fmt.Errorf("engine: unknown configured analysis strategy %q", name)
		}
	}

	configs := make([]strategy.Config, 0, len(known))
	for _, id := range strategy.KnownIDs() {
		path := "analysis.strategies." + string(id)
		entry, err := doc.Section(path)
		if err != nil {
			return nil, fmt.Errorf("engine: required strategy %q: %w", id, err)
		}
		version, err := getString(doc, path+".version")
		if err != nil {
			return nil, err
		}
		enabled, err := getBool(doc, path+".enabled")
		if err != nil {
			return nil, err
		}
		parameters := make(map[string]any, len(entry))
		for key, value := range entry {
			if key != "version" && key != "enabled" {
				parameters[key] = value
			}
		}
		configs = append(configs, strategy.Config{ID: id, Version: version, Enabled: enabled, Parameters: parameters})
	}
	if err := strategy.ValidateConfigs(configs); err != nil {
		return nil, fmt.Errorf("engine: invalid analysis strategy configuration: %w", err)
	}
	return configs, nil
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

// KafkaConfigFromConfig reads runtime.kafka.* into kafka.Config — the
// Kafka transport task's own addition to this file, following the exact
// same discipline as every function above it (Kafka transport task).
// internal/transport/kafka itself never reads config.Document; this is
// the one and only place that translates YAML into kafka.Config.
func KafkaConfigFromConfig(doc *config.Document) (kafka.Config, error) {
	enabled, err := getBool(doc, "runtime.kafka.enabled")
	if err != nil {
		return kafka.Config{}, err
	}
	brokersRaw, ok := doc.Get("runtime.kafka.brokers")
	if !ok {
		return kafka.Config{}, fmt.Errorf("engine: missing required config %q", "runtime.kafka.brokers")
	}
	brokersList, ok := brokersRaw.([]any)
	if !ok {
		return kafka.Config{}, fmt.Errorf("engine: config %q is not a list (got %T)", "runtime.kafka.brokers", brokersRaw)
	}
	brokers := make([]string, 0, len(brokersList))
	for i, b := range brokersList {
		s, ok := b.(string)
		if !ok {
			return kafka.Config{}, fmt.Errorf("engine: runtime.kafka.brokers[%d] is not a string (got %T)", i, b)
		}
		brokers = append(brokers, s)
	}
	clientID, err := getString(doc, "runtime.kafka.client_id.analysis_engine")
	if err != nil {
		return kafka.Config{}, err
	}
	outboxPath, err := getString(doc, "runtime.kafka.outbox_path")
	if err != nil {
		return kafka.Config{}, err
	}
	analysisOpportunity, err := getString(doc, "runtime.kafka.topics.analysis_opportunity")
	if err != nil {
		return kafka.Config{}, err
	}
	analysisOpportunityInvalidated, err := getString(doc, "runtime.kafka.topics.analysis_opportunity_invalidated")
	if err != nil {
		return kafka.Config{}, err
	}
	analysisOpportunityArbitration, err := getString(doc, "runtime.kafka.topics.analysis_opportunity_arbitration")
	if err != nil {
		return kafka.Config{}, err
	}
	topicSpecs, err := kafkaTopicSpecsFromConfig(doc)
	if err != nil {
		return kafka.Config{}, err
	}

	cfg := kafka.Config{
		Enabled: enabled, Brokers: brokers, ClientID: clientID, OutboxPath: outboxPath,
		Topics: kafka.Topics{
			AnalysisOpportunity: analysisOpportunity, AnalysisOpportunityInvalidated: analysisOpportunityInvalidated,
			AnalysisOpportunityArbitration: analysisOpportunityArbitration,
		},
		TopicSpecs: topicSpecs,
	}
	if err := cfg.Validate(); err != nil {
		return kafka.Config{}, err
	}
	return cfg, nil
}

// OpportunityReplayMaxAgeFromConfig returns the maximum age for a persisted
// lifecycle replay before it is no longer eligible to become a new live plan.
// It is the sum of the consumer's confirmed-observation age and the allowed
// Kafka delivery lag, keeping the publisher cleanup aligned with the single
// execution freshness authority in analysis.technical_authority.
func OpportunityReplayMaxAgeFromConfig(doc *config.Document) (time.Duration, error) {
	maxEventAge, err := getInt(doc, "analysis.technical_authority.max_event_age_seconds")
	if err != nil {
		return 0, err
	}
	maxDeliveryLag, err := getInt(doc, "analysis.technical_authority.max_delivery_lag_seconds")
	if err != nil {
		return 0, err
	}
	if maxEventAge <= 0 || maxDeliveryLag < 0 {
		return 0, fmt.Errorf("analysis technical-authority freshness values must be positive")
	}
	return time.Duration(maxEventAge+maxDeliveryLag) * time.Second, nil
}

// RedisConfigFromConfig resolves the market-data transport. Redis owns bars,
// spot state and catch-up; no domain package reads this topology directly.
func RedisConfigFromConfig(doc *config.Document) (redistransport.Config, error) {
	url, err := getString(doc, "runtime.redis.url")
	if err != nil {
		return redistransport.Config{}, err
	}
	channel, err := getString(doc, "runtime.redis.bars_channel")
	if err != nil {
		return redistransport.Config{}, err
	}
	seconds, err := getInt(doc, "runtime.redis.reconciliation_interval_seconds")
	if err != nil {
		return redistransport.Config{}, err
	}
	cfg := redistransport.Config{URL: url, BarsChannel: channel, ReconciliationInterval: time.Duration(seconds) * time.Second}
	if err := cfg.Validate(); err != nil {
		return redistransport.Config{}, err
	}
	return cfg, nil
}

func kafkaTopicSpecsFromConfig(doc *config.Document) (map[string]kafka.TopicSpec, error) {
	raw, ok := doc.Get("runtime.kafka.topic_specs")
	if !ok {
		return nil, fmt.Errorf("engine: missing required config %q", "runtime.kafka.topic_specs")
	}
	entries, ok := raw.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("engine: config %q is not a mapping (got %T)", "runtime.kafka.topic_specs", raw)
	}
	result := make(map[string]kafka.TopicSpec, len(entries))
	for name, value := range entries {
		mapping, ok := value.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("engine: config runtime.kafka.topic_specs.%s is not a mapping (got %T)", name, value)
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
		return 0, fmt.Errorf("engine: missing required config runtime.kafka.topic_specs.%s.%s", topic, key)
	}
	switch typed := value.(type) {
	case int:
		return typed, nil
	case int64:
		return int(typed), nil
	case float64:
		return int(typed), nil
	default:
		return 0, fmt.Errorf("engine: config runtime.kafka.topic_specs.%s.%s is not an integer (got %T)", topic, key, value)
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

// BarrierConfigFromConfig resolves the opposing-barrier normalization policy
// for one instrument. The execution-zone width limits are the same
// execution.policy values Algo Bot's structural barrier book has always
// applied (execution_zone_max_width_atr / _pips); pip size comes from the
// instrument's own contract geometry, never an assumed constant.
func BarrierConfigFromConfig(doc *config.Document, geometry market.Geometry) (barrier.Config, error) {
	maxATR, err := getFloat(doc, "execution.policy.execution_zone_max_width_atr")
	if err != nil {
		return barrier.Config{}, err
	}
	maxPips, err := getFloat(doc, "execution.policy.execution_zone_max_width_pips")
	if err != nil {
		return barrier.Config{}, err
	}
	if maxATR <= 0 || maxPips <= 0 || geometry.PipSize <= 0 {
		return barrier.Config{}, fmt.Errorf("engine: barrier width limits and pip size must be positive (atr=%v pips=%v pip_size=%v)", maxATR, maxPips, geometry.PipSize)
	}
	return barrier.Config{
		Timeframes: barrier.DefaultTimeframes, PipSize: geometry.PipSize, MaxWidthATR: maxATR, MaxWidthPips: maxPips,
	}, nil
}

// MomentumConfigFromConfig reads the price-only momentum thresholds
// (analysis.momentum.*). Every value is required: momentum is used as the
// higher-timeframe bias fallback, so a missing leaf must fail startup rather
// than silently classify with a hidden default.
func MomentumConfigFromConfig(doc *config.Document) (momentum.Config, error) {
	lookback, err := getFloat(doc, "analysis.momentum.velocity_lookback")
	if err != nil {
		return momentum.Config{}, err
	}
	bull, err := getFloat(doc, "analysis.momentum.velocity_bull_threshold")
	if err != nil {
		return momentum.Config{}, err
	}
	bear, err := getFloat(doc, "analysis.momentum.velocity_bear_threshold")
	if err != nil {
		return momentum.Config{}, err
	}
	if lookback < 1 || lookback != float64(int(lookback)) {
		return momentum.Config{}, fmt.Errorf("engine: analysis.momentum.velocity_lookback must be a positive whole number of bars, got %v", lookback)
	}
	if !(bull > 0) || !(bear < 0) {
		return momentum.Config{}, fmt.Errorf("engine: analysis.momentum thresholds must satisfy bear < 0 < bull, got bull=%v bear=%v", bull, bear)
	}
	return momentum.Config{Lookback: int(lookback), BullThreshold: bull, BearThreshold: bear}, nil
}

// LegacyReadConfigFromConfig reads analysis.legacy_read.* — the bounded
// detector-contract windows and construction parameters — and assembles the
// rest of the read's configuration from the shared fib, regime, momentum,
// session and trendline leaves. Instrument-scale values (pip size, round step)
// are applied per instrument by ApplyInstrument.
func LegacyReadConfigFromConfig(doc *config.Document, atr ATRSettings, fibConfig fib.Config, regimeConfig regime.Config, momentumConfig momentum.Config, sessionConfig session.Config, trendlineConfig trendline.Config) (legacyread.Config, error) {
	cfg := legacyread.Config{
		ATRLength: atr.Length, Fib: fibConfig, Regime: regimeConfig, Momentum: momentumConfig,
		Session: sessionConfig, Trendline: trendlineConfig, WindowBars: map[market.Timeframe]int{},
	}
	floats := map[string]*float64{
		"swing.zigzag_pct": &cfg.ZigzagPct, "swing.zigzag_atr_mult": &cfg.ZigzagATRMult,
		"level.cluster_atr": &cfg.LevelClusterATR, "level.max_cluster_span_multiple": &cfg.MaximumClusterSpanMultiple,
		"displacement.atr_mult": &cfg.DisplacementATRMult, "displacement.body_fraction": &cfg.MomentumBodyFraction,
		"zone.merge_overlap": &cfg.ZoneMergeOverlap, "zone.max_merged_zone_atr": &cfg.MaximumMergedZoneATR,
		"zone.flip_band_body_fraction": &cfg.FlipBandBodyFraction,
		"liquidity.equal_tol_atr":      &cfg.EqualToleranceATR, "liquidity.inducement_band_atr": &cfg.InducementBandATR,
		"liquidity.sweep_body_fraction": &cfg.SweepBodyFraction,
	}
	ints := map[string]*int{
		"swing.fractal_n": &cfg.FractalN, "min_primary_htf_warmup_bars": &cfg.MinPrimaryHTFWarmupBars,
		"level.min_touches": &cfg.LevelMinimumTouches, "zone.flip_accept_bars": &cfg.FlipAcceptBars,
		"zone.flip_max_break_age_bars": &cfg.FlipMaximumBreakAgeBars, "liquidity.sweep_react_bars": &cfg.SweepReactBars,
	}
	var err error
	if cfg.Scalp, err = ScalpConfigFromConfig(doc); err != nil {
		return legacyread.Config{}, err
	}
	if cfg.Compat, err = compatConfigFromConfig(doc); err != nil {
		return legacyread.Config{}, err
	}
	for key, dst := range floats {
		if *dst, err = getFloat(doc, "analysis.legacy_read."+key); err != nil {
			return legacyread.Config{}, err
		}
	}
	for key, dst := range ints {
		if *dst, err = getInt(doc, "analysis.legacy_read."+key); err != nil {
			return legacyread.Config{}, err
		}
	}
	if cfg.AllowCounterTrend, err = getBool(doc, "analysis.legacy_read.allow_counter_trend"); err != nil {
		return legacyread.Config{}, err
	}
	if cfg.ZoneWidth, err = getString(doc, "analysis.legacy_read.zone.width"); err != nil {
		return legacyread.Config{}, err
	}
	order, err := getString(doc, "analysis.legacy_read.htf_order")
	if err != nil {
		return legacyread.Config{}, err
	}
	for _, name := range strings.Split(order, ",") {
		tf, err := market.ParseTimeframe(strings.TrimSpace(name))
		if err != nil {
			return legacyread.Config{}, fmt.Errorf("engine: analysis.legacy_read.htf_order: %w", err)
		}
		cfg.HTFOrder = append(cfg.HTFOrder, tf)
	}
	section, err := doc.Section("analysis.legacy_read.window_bars")
	if err != nil {
		return legacyread.Config{}, err
	}
	for key := range section {
		tf, err := market.ParseTimeframe(key)
		if err != nil {
			return legacyread.Config{}, fmt.Errorf("engine: analysis.legacy_read.window_bars.%s: %w", key, err)
		}
		if cfg.WindowBars[tf], err = getInt(doc, "analysis.legacy_read.window_bars."+key); err != nil {
			return legacyread.Config{}, err
		}
	}
	if cfg.ATRLength < 1 || cfg.FractalN < 1 || cfg.ZigzagPct < 0 || cfg.ZigzagATRMult < 0 || cfg.MinPrimaryHTFWarmupBars < 0 || len(cfg.HTFOrder) == 0 || len(cfg.WindowBars) == 0 ||
		cfg.LevelClusterATR <= 0 || cfg.LevelMinimumTouches < 1 || cfg.MaximumClusterSpanMultiple < 0 || cfg.DisplacementATRMult <= 0 ||
		cfg.MomentumBodyFraction < 0 || cfg.MomentumBodyFraction > 1 || (cfg.ZoneWidth != "body" && cfg.ZoneWidth != "range") ||
		cfg.ZoneMergeOverlap < 0 || cfg.ZoneMergeOverlap > 1 || cfg.MaximumMergedZoneATR < 0 || cfg.FlipAcceptBars < 1 ||
		cfg.FlipMaximumBreakAgeBars < 0 || cfg.FlipBandBodyFraction < 0 || cfg.FlipBandBodyFraction > 1 ||
		cfg.EqualToleranceATR < 0 || cfg.InducementBandATR < 0 || cfg.SweepBodyFraction < 0 || cfg.SweepBodyFraction > 1 || cfg.SweepReactBars < 0 {
		return legacyread.Config{}, fmt.Errorf("engine: analysis.legacy_read has an invalid value")
	}
	for tf, bars := range cfg.WindowBars {
		if bars < 50 {
			return legacyread.Config{}, fmt.Errorf("engine: analysis.legacy_read.window_bars.%s must be at least 50", tf)
		}
	}
	return cfg, nil
}

// LegacyDetectorParametersFromConfig reads analysis.legacy_read.detector.*, the
// shared decision thresholds of the frozen detectors, as strategy parameters.
func LegacyDetectorParametersFromConfig(doc *config.Document) (map[string]any, error) {
	params := map[string]any{}
	for key, name := range map[string]string{
		"maximum_entry_atr": "maximum_entry_atr", "maximum_zone_width_atr": "maximum_zone_width_atr", "proximal_band_atr": "proximal_band_atr",
		"engulfing_minimum_range_atr": "engulfing_minimum_range_atr",
	} {
		value, err := getFloat(doc, "analysis.legacy_read.detector."+key)
		if err != nil {
			return nil, err
		}
		params[name] = value
	}
	for _, key := range []string{"confluence_floor", "reaction_lookback_bars"} {
		value, err := getInt(doc, "analysis.legacy_read.detector."+key)
		if err != nil {
			return nil, err
		}
		params[key] = float64(value)
	}
	enabled, err := getBool(doc, "analysis.legacy_read.detector.fibonacci_enabled")
	if err != nil {
		return nil, err
	}
	params["fibonacci_enabled"] = enabled
	return params, nil
}

// ScalpConfigFromConfig reads the range-edge structure leaves from
// analysis.strategies.range_edge, the one source of truth shared by the
// structure builder and the strategy. Instrument-scale values are applied per
// instrument.
func ScalpConfigFromConfig(doc *config.Document) (techniquezone.ScalpConfig, error) {
	var cfg techniquezone.ScalpConfig
	base := "analysis.strategies.range_edge."
	floats := map[string]*float64{
		"cluster_atr": &cfg.ClusterATR, "cluster_min_abs": &cfg.ClusterMinAbs, "cluster_pip_mult": &cfg.ClusterPipMult,
		"minimum_wick_fraction": &cfg.MinimumWickFraction, "entry_tolerance_atr": &cfg.EntryToleranceATR,
		"maximum_edge_width_atr": &cfg.MaximumEdgeWidthATR, "minimum_width_atr": &cfg.MinimumWidthATR,
		"maximum_width_atr": &cfg.MaximumWidthATR, "minimum_room_atr": &cfg.MinimumRoomATR,
		"recent_breakout_buffer_atr": &cfg.RecentBreakoutBufferATR, "recent_breakout_min_span_atr": &cfg.RecentBreakoutMinSpanATR,
		"fallback_min_width_atr": &cfg.FallbackMinWidthATR, "fallback_max_width_atr": &cfg.FallbackMaxWidthATR,
		"fallback_wick_fraction": &cfg.FallbackWickFraction, "post_impulse_min_displacement_atr": &cfg.PostImpulseMinDisplaceATR,
		"post_impulse_max_contraction_atr": &cfg.PostImpulseMaxContractATR,
	}
	ints := map[string]*int{
		"lookback_bars": &cfg.Lookback, "minimum_touches": &cfg.MinimumTouches, "break_closes": &cfg.BreakCloses,
		"minimum_inside_closes": &cfg.MinimumInsideCloses, "inside_lookback_bars": &cfg.InsideLookbackBars,
		"recent_breakout_lookback_bars": &cfg.RecentBreakoutLookback, "fallback_minimum_confirmations": &cfg.FallbackMinConfirmations,
		"post_impulse_min_inside_closes": &cfg.PostImpulseMinInside, "post_impulse_lookback_bars": &cfg.PostImpulseLookbackBars,
		"post_impulse_recent_bars": &cfg.PostImpulseRecentBars,
	}
	bools := map[string]*bool{
		"fallback_enabled": &cfg.FallbackEnabled, "provisional_enabled": &cfg.ProvisionalEnabled, "post_impulse_enabled": &cfg.PostImpulseEnabled,
	}
	var err error
	for key, dst := range floats {
		if *dst, err = getFloat(doc, base+key); err != nil {
			return cfg, err
		}
	}
	for key, dst := range ints {
		if *dst, err = getInt(doc, base+key); err != nil {
			return cfg, err
		}
	}
	for key, dst := range bools {
		if *dst, err = getBool(doc, base+key); err != nil {
			return cfg, err
		}
	}
	return cfg, nil
}

// compatConfigFromConfig reads analysis.legacy_read.compat.*: the default
// arguments of the frozen compat helpers (see techniquezone.CompatConfig).
func compatConfigFromConfig(doc *config.Document) (techniquezone.CompatConfig, error) {
	var cfg techniquezone.CompatConfig
	base := "analysis.legacy_read.compat."
	var err error
	for key, dst := range map[string]*float64{
		"pip_size": &cfg.PipSize, "displacement_body_fraction": &cfg.DisplacementBodyFraction, "displacement_atr_mult": &cfg.DisplacementATRMult,
		"level_cluster_atr": &cfg.LevelClusterATR, "round_step": &cfg.RoundStep, "max_cluster_span_multiple": &cfg.MaximumClusterSpanMultiple,
	} {
		if *dst, err = getFloat(doc, base+key); err != nil {
			return cfg, err
		}
	}
	for key, dst := range map[string]*int{"fractal_n": &cfg.FractalN, "atr_length": &cfg.ATRLength} {
		if *dst, err = getInt(doc, base+key); err != nil {
			return cfg, err
		}
	}
	if cfg.PipSize <= 0 || cfg.DisplacementBodyFraction < 0 || cfg.DisplacementBodyFraction > 1 || cfg.DisplacementATRMult <= 0 ||
		cfg.LevelClusterATR <= 0 || cfg.RoundStep <= 0 || cfg.MaximumClusterSpanMultiple < 0 || cfg.FractalN < 1 || cfg.ATRLength < 1 {
		return cfg, fmt.Errorf("engine: analysis.legacy_read.compat has an invalid value")
	}
	return cfg, nil
}
