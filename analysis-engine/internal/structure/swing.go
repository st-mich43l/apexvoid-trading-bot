package structure

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// SwingKind aliases PivotKind — a Swing's kind is always inherited
// directly from the Pivot it was promoted from, never independently
// classified.
type SwingKind = PivotKind

const (
	SwingHigh = PivotHigh
	SwingLow  = PivotLow
)

// StructureLayer is the explicit structural hierarchy (source task §13):
// timeframe is evidence toward a swing's significance, never the sole
// definition of it — a large M5 swing may outrank a tiny M15 fluctuation.
// Layer is assigned purely from ExcursionATR via PromotionConfig (below),
// never from SourceTimeframe.
type StructureLayer uint8

const (
	StructureMicro StructureLayer = iota
	StructureInternal
	StructureIntermediate
	StructureMajor
)

func (l StructureLayer) String() string {
	switch l {
	case StructureMicro:
		return "micro"
	case StructureInternal:
		return "internal"
	case StructureIntermediate:
		return "intermediate"
	case StructureMajor:
		return "major"
	default:
		return "unknown"
	}
}

// Swing is a confirmed, promoted pivot — source task §14. Carries enough
// metadata for later strategies to reason about it without recomputing
// anything: ExcursionPrice/ExcursionATR are the raw and normalized
// significance that earned its Layer, BarsToConfirm records how many
// right-side bars its confirmation actually required (the pivot detector's
// rightBars parameter, at the time this swing was produced — kept on the
// swing itself so a later change to that setting doesn't retroactively
// misdescribe an already-produced swing).
type Swing struct {
	ID string

	Kind  SwingKind
	Layer StructureLayer

	Time  int64
	Price market.Price

	SourceTimeframe market.Timeframe

	ExcursionPrice float64
	ExcursionATR   float64

	BarsToConfirm int

	Strength float64

	ConfirmedAt int64
}

// PromotionConfig is Structure V2's config-driven promotion ladder
// (config/analysis.yml's analysis.structure.swing.*) — source task §16:
// "all tunable values belong in Configuration V3. No hidden XAU thresholds
// in Go." Every threshold is in ATR units, never a raw price distance, so
// the same config works unmodified for XAU (pip 0.1) and a 5-digit FX
// pair (pip 0.0001) alike — proven in test/structure/instrument_test.go
// across XAU/EURUSD/USDJPY fixtures sharing one PromotionConfig.
type PromotionConfig struct {
	MinimumExcursionATR float64
	InternalATR         float64
	IntermediateATR     float64
	MajorATR            float64
}

// PromoteSwing turns a confirmed Pivot into a Swing, or reports false when
// the pivot's excursion never clears MinimumExcursionATR (source task
// §12/§15: not every fractal is a meaningful swing). Layer is the highest
// rung the pivot's ExcursionATR clears — deterministic, no ties broken by
// anything other than the configured thresholds (§15: "promotion must be
// deterministic").
func PromoteSwing(p Pivot, tf market.Timeframe, rightBars int, cfg PromotionConfig) (Swing, bool) {
	if p.Strength < cfg.MinimumExcursionATR {
		return Swing{}, false
	}
	layer := StructureMicro
	switch {
	case p.Strength >= cfg.MajorATR:
		layer = StructureMajor
	case p.Strength >= cfg.IntermediateATR:
		layer = StructureIntermediate
	case p.Strength >= cfg.InternalATR:
		layer = StructureInternal
	}
	return Swing{
		ID:              swingID(tf, p),
		Kind:            p.Kind,
		Layer:           layer,
		Time:            p.Time,
		Price:           p.Price,
		SourceTimeframe: tf,
		ExcursionPrice:  p.ExcursionPrice,
		ExcursionATR:    p.Strength,
		BarsToConfirm:   rightBars,
		Strength:        p.Strength,
		ConfirmedAt:     p.ConfirmedAt,
	}, true
}

func swingID(tf market.Timeframe, p Pivot) string {
	return fmt.Sprintf("%s:%s:%d", tf, p.Kind, p.Time)
}
