package indicator

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Algorithm selects the canonical ATR formula from
// config/analysis.yml's analysis.indicators.atr.algorithm enum.
type Algorithm string

const (
	AlgorithmSimple Algorithm = "simple"
	AlgorithmWilder Algorithm = "wilder"
)

// CanonicalATR computes ATR using ONE, config-selected algorithm — never a
// second, independently-chosen formula elsewhere in the pipeline (source
// task §27). This is the direct fix for the real, measured production bug
// The two ATR formulas
// diverge ~6.7% on real XAU M5 data, and the legacy Python system computed
// zone/level/swing geometry on one and detector confirmation geometry on
// the other, in the same detection pass, for the same candles. Structure
// V2 (internal/structure) must be given a series from this function, never
// call SimpleATR/WilderATR directly.
//
// An unrecognized algorithm fails closed (source task §58) — it is never
// silently treated as "simple."
func CanonicalATR(candles []market.Candle, length int, algorithm Algorithm) ([]float64, error) {
	switch algorithm {
	case AlgorithmSimple:
		return SimpleATR(candles, length), nil
	case AlgorithmWilder:
		series, ok := WilderATR(candles, length)
		if !ok {
			return nil, fmt.Errorf(
				"indicator: insufficient candles for Wilder ATR (need > %d, got %d)", length, len(candles),
			)
		}
		return series, nil
	default:
		return nil, fmt.Errorf(
			"indicator: unsupported ATR algorithm %q — must be %q or %q, no silent fallback",
			algorithm, AlgorithmSimple, AlgorithmWilder,
		)
	}
}
