package engine

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
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
