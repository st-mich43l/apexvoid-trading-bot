package engine

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
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
	settings.DefendedLevels = levels
	settings.DefendedLevelBuffer = buffer
	return applyKeyLevelOverrides(settings, doc, symbol)
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
