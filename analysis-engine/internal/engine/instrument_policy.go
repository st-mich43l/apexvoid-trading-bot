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
