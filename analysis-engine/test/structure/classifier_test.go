package structure_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func sw(kind structure.SwingKind, price float64) structure.Swing {
	return structure.Swing{Kind: kind, Price: market.Price(price)}
}

func TestClassifySwingRelation(t *testing.T) {
	const atr = 10.0
	const tol = 0.05 // tolerance band = 0.5

	cases := []struct {
		name      string
		cur, prev structure.Swing
		want      structure.SwingRelation
	}{
		{"higher high", sw(structure.SwingHigh, 110), sw(structure.SwingHigh, 100), structure.HigherHigh},
		{"lower high", sw(structure.SwingHigh, 90), sw(structure.SwingHigh, 100), structure.LowerHigh},
		{"higher low", sw(structure.SwingLow, 105), sw(structure.SwingLow, 100), structure.HigherLow},
		{"lower low", sw(structure.SwingLow, 95), sw(structure.SwingLow, 100), structure.LowerLow},
		{"equal high within tolerance", sw(structure.SwingHigh, 100.3), sw(structure.SwingHigh, 100), structure.EqualHigh},
		{"equal low within tolerance", sw(structure.SwingLow, 99.7), sw(structure.SwingLow, 100), structure.EqualLow},
		{"just outside tolerance is a real HH, not equal", sw(structure.SwingHigh, 100.6), sw(structure.SwingHigh, 100), structure.HigherHigh},
		{"mismatched kind is unknown", sw(structure.SwingHigh, 110), sw(structure.SwingLow, 100), structure.RelationUnknown},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := structure.ClassifySwingRelation(tc.cur, tc.prev, atr, tol)
			if got != tc.want {
				t.Errorf("got %v, want %v", got, tc.want)
			}
		})
	}
}

func TestClassifySwingRelation_ToleranceScalesWithATR(t *testing.T) {
	// Same 0.3 raw price diff: "equal" at high ATR (wide tolerance band),
	// a real relation at low ATR (narrow band) — source task §18's exact
	// point: no direct floating-point equality, an instrument/volatility-
	// aware band instead.
	cur, prev := sw(structure.SwingHigh, 100.3), sw(structure.SwingHigh, 100)
	if got := structure.ClassifySwingRelation(cur, prev, 100.0, 0.05); got != structure.EqualHigh {
		t.Errorf("high ATR (tol=5.0): expected EqualHigh, got %v", got)
	}
	if got := structure.ClassifySwingRelation(cur, prev, 1.0, 0.05); got != structure.HigherHigh {
		t.Errorf("low ATR (tol=0.05): expected HigherHigh, got %v", got)
	}
}
