package engine

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

// ApplyInstrument sets every per-symbol field of Settings (LoadSettings is
// symbol-agnostic): the instrument geometry and its defended-level policy.
func ApplyInstrument(settings *Settings, doc *config.Document, symbol string) error {
	geometry, err := doc.GeometryFor(symbol)
	if err != nil {
		return fmt.Errorf("loading instrument geometry for %s: %w", symbol, err)
	}
	levels, buffer, err := doc.DefendedLevelsFor(symbol)
	if err != nil {
		return fmt.Errorf("loading defended levels for %s: %w", symbol, err)
	}
	settings.Geometry = geometry
	stopMinPips, stopMaxPips, err := doc.StopEnvelopeFor(symbol)
	if err != nil {
		return fmt.Errorf("loading stop envelope for %s: %w", symbol, err)
	}
	settings.InstrumentStopMinPips = stopMinPips
	settings.InstrumentStopMaxPips = stopMaxPips
	settings.InstrumentStopEnvelopeConfigured = true
	settings.TechniqueZones.Technique.PipSize = geometry.PipSize
	if entryMax, ok, err := doc.InstrumentValue(symbol, "price_scale", "fvg_entry_max_width_price"); err != nil {
		return err
	} else if ok {
		switch v := entryMax.(type) {
		case float64:
			settings.TechniqueZones.Technique.FVGEntryMaxWidthPrice = v
		case int:
			settings.TechniqueZones.Technique.FVGEntryMaxWidthPrice = float64(v)
		default:
			return fmt.Errorf("instrument %s: fvg_entry_max_width_price must be a number, got %T", symbol, entryMax)
		}
	}
	settings.LegacyRead.PipSize = geometry.PipSize
	settings.LegacyRead.RoundStep = settings.KeyLevel.RoundStep
	if roundStep, ok, err := doc.InstrumentValue(symbol, "price_scale", "round_step"); err != nil {
		return err
	} else if ok {
		switch v := roundStep.(type) {
		case float64:
			settings.LegacyRead.RoundStep = v
		case int:
			settings.LegacyRead.RoundStep = float64(v)
		default:
			return fmt.Errorf("instrument %s: round_step must be a number, got %T", symbol, roundStep)
		}
	}
	settings.DefendedLevels = levels
	settings.DefendedLevelBuffer = buffer
	if err := applyTechniqueGeometry(settings, doc, symbol); err != nil {
		return err
	}
	applyParityCRT(settings)
	applyLegacyDetector(settings)
	if err := applyScalpBreakoutRetest(settings, doc); err != nil {
		return err
	}
	if err := applyObserveOnly(settings, doc, symbol); err != nil {
		return err
	}
	return applyKeyLevelOverrides(settings, doc, symbol)
}

// applyParityCRT supplies the per-instrument geometry required by the exact
// Go port of Python CRT. This is unconditional behavior, not a mode switch.
func applyParityCRT(settings *Settings) {
	for i := range settings.Strategies {
		if settings.Strategies[i].ID != "crt" {
			continue
		}
		params := make(map[string]any, len(settings.Strategies[i].Parameters)+5)
		for k, v := range settings.Strategies[i].Parameters {
			params[k] = v
		}
		params["technique_window_bars"] = float64(settings.TechniqueZones.WindowBars)
		params["entry_max_width_price"] = settings.TechniqueZones.Technique.FVGEntryMaxWidthPrice
		params["pip_size"] = settings.TechniqueZones.Technique.PipSize
		params["reaction_lookback_bars"] = 3.0
		params["engulfing_minimum_range_atr"] = 0.5
		settings.Strategies[i].Parameters = params
	}
}

// techniqueStrategies are the zone strategies whose confirmed reactions pass
// the legacy technique validation.
var techniqueStrategies = map[strategy.StrategyID]bool{
	"supply": true, "demand": true, "order_block": true, "fvg": true, "ifvg": true,
}

// applyTechniqueGeometry gives those strategies the instrument's pip size and
// its entry-width cap (instrument price_scale.fvg_entry_max_width_price, which
// the legacy detectors read for every clipped technique), when declared.
func applyTechniqueGeometry(settings *Settings, doc *config.Document, symbol string) error {
	entryMax, hasEntryMax, err := doc.InstrumentValue(symbol, "price_scale", "fvg_entry_max_width_price")
	if err != nil {
		return err
	}
	for i := range settings.Strategies {
		if !techniqueStrategies[settings.Strategies[i].ID] {
			continue
		}
		params := make(map[string]any, len(settings.Strategies[i].Parameters)+2)
		for k, v := range settings.Strategies[i].Parameters {
			params[k] = v
		}
		params["pip_size"] = settings.Geometry.PipSize
		if hasEntryMax {
			switch v := entryMax.(type) {
			case float64:
				params["entry_max_width_price"] = v
			case int:
				params["entry_max_width_price"] = float64(v)
			default:
				return fmt.Errorf("instrument %s: fvg_entry_max_width_price must be a number, got %T", symbol, entryMax)
			}
		}
		settings.Strategies[i].Parameters = params
	}
	return nil
}

// applyKeyLevelOverrides carries Python's per-instrument Key Level quality
// rules into this symbol's strategy parameters. Added for GBPJPY on 2026-08-25
// after Key Level went 0/4 (-118 pips): at least 3 touches
// (overrides.analysis.levels.minimum_key_touches) and an explicit role, i.e.
// skip levels whose support/resistance role is still ambiguous
// (overrides.auto_algo.strategies.reaction.key_level.require_explicit_role).
// The override tree's key_level.min_grade is not carried: nothing in the
// Python runtime ever read it.
func applyKeyLevelOverrides(settings *Settings, doc *config.Document, symbol string) error {
	touches, hasTouches, err := doc.InstrumentOverride(symbol, "analysis", "levels", "minimum_key_touches")
	if err != nil {
		return err
	}
	explicit, hasExplicit, err := doc.InstrumentOverride(symbol, "auto_algo", "strategies", "reaction", "key_level", "require_explicit_role")
	if err != nil {
		return err
	}
	if !hasTouches && !hasExplicit {
		return nil
	}
	for i := range settings.Strategies {
		if settings.Strategies[i].ID != "key_level" {
			continue
		}
		params := make(map[string]any, len(settings.Strategies[i].Parameters)+2)
		for k, v := range settings.Strategies[i].Parameters {
			params[k] = v
		}
		if hasTouches {
			switch v := touches.(type) {
			case int:
				params["minimum_touches"] = float64(v)
			case float64:
				params["minimum_touches"] = v
			default:
				return fmt.Errorf("instrument %s: minimum_key_touches must be a number, got %T", symbol, touches)
			}
		}
		if hasExplicit {
			b, ok := explicit.(bool)
			if !ok {
				return fmt.Errorf("instrument %s: require_explicit_role must be a boolean, got %T", symbol, explicit)
			}
			params["require_explicit_role"] = b
		}
		settings.Strategies[i].Parameters = params
	}
	return nil
}

// BlockedByDefendedLevel reports whether a candidate must never be opened
// because it buys into a price an authority defends.
//
// 2026 USDJPY: Japan/the US ran a record joint intervention when the pair
// breached 160, and intervention sells USDJPY, so the asymmetric risk is a
// LONG into that ceiling. A SELL near the level is aligned with the
// intervention and stays eligible; only a BUY whose entry zone lies within
// the buffer of a defended level is blocked. (The buffer is deliberately
// narrow: a symmetric 100-pip band once blocked every plan, SELLs included,
// at 159.4.)
func (s Settings) BlockedByDefendedLevel(c opportunity.Candidate) (float64, bool) {
	if c.Direction == market.Sell || len(s.DefendedLevels) == 0 || !(s.DefendedLevelBuffer > 0) {
		return 0, false
	}
	for _, level := range s.DefendedLevels {
		distance := 0.0
		switch {
		case level < c.Entry.Low:
			distance = c.Entry.Low - level
		case level > c.Entry.High:
			distance = level - c.Entry.High
		}
		if distance <= s.DefendedLevelBuffer+1e-9 { // tolerance for binary price noise at the exact edge
			return level, true
		}
	}
	return 0, false
}

// legacyDetectorStrategies are the strategies whose decision is the frozen
// detector contract's and which therefore share its thresholds.
var legacyDetectorStrategies = map[string]bool{
	"snap_back": true, "fade_scalp": true, "momentum_ride": true, "break_retest": true, "range_edge": true, "key_level": true, "liquidity_sweep": true,
}

// applyLegacyDetector injects the shared frozen-detector thresholds, the
// instrument scale and the shared confluence/fibonacci contract into the
// detector-contract strategies' parameters. It is idempotent.
func applyLegacyDetector(settings *Settings) {
	for i := range settings.Strategies {
		if !legacyDetectorStrategies[string(settings.Strategies[i].ID)] {
			continue
		}
		params := make(map[string]any, len(settings.Strategies[i].Parameters)+len(settings.LegacyDetector)+10)
		for k, v := range settings.Strategies[i].Parameters {
			params[k] = v
		}
		for k, v := range settings.LegacyDetector {
			params[k] = v
		}
		params["pip_size"] = settings.LegacyRead.PipSize
		params["fvg_entry_max_width_price"] = settings.TechniqueZones.Technique.FVGEntryMaxWidthPrice
		params["confluence_scoring_version"] = settings.Confluence.ScoringVersion
		params["confluence_star_three_ratio"] = settings.Confluence.StarThreeRatio
		params["confluence_star_two_ratio"] = settings.Confluence.StarTwoRatio
		params["confluence_zone_quality_weight"] = settings.Confluence.ZoneQualityWeight
		params["confluence_mad_score_weight"] = settings.Confluence.MADScoreWeight
		params["fibonacci_confluence_weight"] = settings.Confluence.FibonacciWeight
		params["fibonacci_epsilon_atr"] = settings.Fib.EpsilonATR
		params["price_digits"] = float64(settings.Geometry.PriceDigits)
		settings.Strategies[i].Parameters = params
	}
}

// scalpBook are the shared M1-scalp stop/target leaves every strategy of the
// M5-setup / M1-confirmation lane reads (auto_algo.strategies.scalping.*).
var scalpBook = map[string]string{
	"buffer_m1_atr_multiple":         "auto_algo.strategies.scalping.stop.buffer_m1_atr_multiple",
	"buffer_minimum_spread_multiple": "auto_algo.strategies.scalping.stop.buffer_minimum_spread_multiple",
	"stop_minimum_pips":              "auto_algo.strategies.scalping.stop.minimum_pips",
	"stop_maximum_pips":              "auto_algo.strategies.scalping.stop.maximum_pips",
	"minimum_net_target_pips":        "auto_algo.strategies.scalping.target.minimum_net_target_pips",
	"maximum_spread_pips":            "auto_algo.strategies.scalping.policy.maximum_spread_pips",
}

// scalpLaneExtras are the leaves only one strategy of the lane reads.
var scalpLaneExtras = map[string]map[string]string{
	"scalp_breakout_retest": {},
	"range_sweep": {
		"buy_maximum_position":     "auto_algo.strategies.scalping.location.range_buy_maximum_position",
		"sell_minimum_position":    "auto_algo.strategies.scalping.location.range_sell_minimum_position",
		"trigger_maximum_age_bars": "auto_algo.strategies.scalping.activation.trigger_maximum_age_bars",
	},
}

// applyScalpBreakoutRetest gives the M5-setup / M1-confirmation scalps (Breakout
// Retest Scalp, Range Sweep) their instrument scale and the shared M1-scalp
// stop/target book (auto_algo.strategies.scalping.*), the same leaves the stop
// envelope and the execution policy read, so a setup is sized from one source.
// It is idempotent.
func applyScalpBreakoutRetest(settings *Settings, doc *config.Document) error {
	for i := range settings.Strategies {
		extras, isScalp := scalpLaneExtras[string(settings.Strategies[i].ID)]
		if !isScalp {
			continue
		}
		params := make(map[string]any, len(settings.Strategies[i].Parameters)+len(scalpBook)+len(extras)+2)
		for k, v := range settings.Strategies[i].Parameters {
			params[k] = v
		}
		for _, leaves := range []map[string]string{scalpBook, extras} {
			for key, path := range leaves {
				value, err := getFloat(doc, path)
				if err != nil {
					return err
				}
				params[key] = value
			}
		}
		pip, digits := settings.Geometry.PipSize, settings.Geometry.PriceDigits
		if pip <= 0 {
			// Before a symbol is attached the canonical defaults stand in.
			pip, digits = settings.LegacyRead.PipSize, 2
		}
		params["pip_size"] = pip
		params["price_digits"] = float64(digits)
		settings.Strategies[i].Parameters = params
	}
	return nil
}

// applyObserveOnly reads the instrument's observe-only strategy list.
func applyObserveOnly(settings *Settings, doc *config.Document, symbol string) error {
	raw, ok, err := doc.InstrumentOverride(symbol, "execution", "go_opportunity", "observe_only_strategies")
	if err != nil {
		return err
	}
	settings.ObserveOnly = nil
	if !ok {
		return nil
	}
	list, isList := raw.([]any)
	if !isList {
		return fmt.Errorf("instrument %s: observe_only_strategies must be a list, got %T", symbol, raw)
	}
	known := map[opportunity.StrategyID]bool{}
	for _, id := range strategy.KnownIDs() {
		known[id] = true
	}
	settings.ObserveOnly = make(map[opportunity.StrategyID]bool, len(list))
	for _, item := range list {
		name, isString := item.(string)
		if !isString || !known[opportunity.StrategyID(name)] {
			return fmt.Errorf("instrument %s: observe_only_strategies names unknown strategy %v", symbol, item)
		}
		settings.ObserveOnly[opportunity.StrategyID(name)] = true
	}
	return nil
}
