package structure_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// Real production pip geometry (config/instruments.yml, read this session
// while wiring config/analysis.yml's Structure V2 leaves): XAU 0.1/2
// digits, a non-JPY FX pair (EURUSD) 0.0001/5 digits, a JPY cross
// (USDJPY) 0.01/3 digits — source task §51's exact three-instrument-class
// coverage requirement.
var instrumentFixtures = []struct {
	name  string
	price float64
	pip   float64
}{
	{"XAU", 4340.0, 0.1},
	{"EURUSD", 1.0850, 0.0001},
	{"USDJPY", 157.20, 0.01},
}

// TestPromoteSwing_LayerIsInstrumentIndependent proves source task §16:
// "normalize using ATR... no hidden XAU thresholds in Go." The exact same
// PromotionConfig, given the exact same ATR-relative excursion (2.5 ATR —
// between IntermediateATR and MajorATR), must promote to the SAME layer
// whether the raw price excursion is $10.85 (XAU), 0.00271 (EURUSD), or
// 0.3125 (USDJPY) — three wildly different absolute magnitudes that
// represent the identical relative significance.
func TestPromoteSwing_LayerIsInstrumentIndependent(t *testing.T) {
	cfg := defaultPromotion()
	for _, inst := range instrumentFixtures {
		t.Run(inst.name, func(t *testing.T) {
			atr := 20 * inst.pip
			excursion := 2.5 * atr
			p := structure.Pivot{
				Kind: structure.PivotHigh, Price: market.Price(inst.price),
				ExcursionPrice: excursion, Strength: excursion / atr,
				Time: 1, ConfirmedAt: 3,
			}
			sw, ok := structure.PromoteSwing(p, "M5", 2, cfg)
			if !ok {
				t.Fatalf("%s: expected promotion", inst.name)
			}
			if sw.Layer != structure.StructureIntermediate {
				t.Errorf("%s: expected StructureIntermediate at 2.5 ATR excursion (price=%v, atr=%v, raw excursion=%v), got %v",
					inst.name, inst.price, atr, excursion, sw.Layer)
			}
		})
	}
}

// TestClassifySwingRelation_EqualityIsInstrumentIndependent proves the
// same point for the equal-high/equal-low tolerance band (§18/§31): the
// same 0.03-ATR raw difference reads as "equal" for every instrument, at
// whatever its own absolute price/pip scale happens to be.
func TestClassifySwingRelation_EqualityIsInstrumentIndependent(t *testing.T) {
	const toleranceATR = 0.05
	for _, inst := range instrumentFixtures {
		t.Run(inst.name, func(t *testing.T) {
			atr := 20 * inst.pip
			diff := 0.03 * atr // inside the 0.05*atr tolerance band
			cur := sw(structure.SwingHigh, inst.price+diff)
			prev := sw(structure.SwingHigh, inst.price)
			if got := structure.ClassifySwingRelation(cur, prev, atr, toleranceATR); got != structure.EqualHigh {
				t.Errorf("%s: expected EqualHigh for a 0.03 ATR diff inside a 0.05 ATR band, got %v", inst.name, got)
			}
		})
	}
}
