package structure_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

func defaultPromotion() structure.PromotionConfig {
	return structure.PromotionConfig{
		MinimumExcursionATR: 0.5, InternalATR: 1.0, IntermediateATR: 2.0, MajorATR: 3.5,
	}
}

func TestPromoteSwing_BelowMinimumExcursionIsRejected(t *testing.T) {
	p := structure.Pivot{Kind: structure.PivotHigh, Strength: 0.3, ExcursionPrice: 3, Time: 10, ConfirmedAt: 20}
	if _, ok := structure.PromoteSwing(p, "M5", 2, defaultPromotion()); ok {
		t.Fatal("expected rejection below MinimumExcursionATR")
	}
}

func TestPromoteSwing_LayerThresholds(t *testing.T) {
	cfg := defaultPromotion()
	cases := []struct {
		strength float64
		want     structure.StructureLayer
	}{
		{0.6, structure.StructureMicro},        // >= min(0.5), < internal(1.0)
		{1.0, structure.StructureInternal},     // >= internal, < intermediate(2.0)
		{2.0, structure.StructureIntermediate}, // >= intermediate, < major(3.5)
		{3.5, structure.StructureMajor},        // >= major
		{10.0, structure.StructureMajor},
	}
	for _, tc := range cases {
		p := structure.Pivot{Kind: structure.PivotHigh, Strength: tc.strength, Time: 1, ConfirmedAt: 2}
		sw, ok := structure.PromoteSwing(p, "M5", 2, cfg)
		if !ok {
			t.Fatalf("strength %v should be promoted", tc.strength)
		}
		if sw.Layer != tc.want {
			t.Errorf("strength %v: got layer %v want %v", tc.strength, sw.Layer, tc.want)
		}
	}
}

func TestPromoteSwing_CarriesFieldsThrough(t *testing.T) {
	p := structure.Pivot{
		Kind: structure.PivotLow, Price: 100, Time: 5, ConfirmedAt: 7,
		ExcursionPrice: 4, Strength: 2.0,
	}
	sw, ok := structure.PromoteSwing(p, "M15", 2, defaultPromotion())
	if !ok {
		t.Fatal("expected promotion")
	}
	if sw.Kind != structure.PivotLow {
		t.Error("Kind mismatch")
	}
	if float64(sw.Price) != 100 {
		t.Error("Price mismatch")
	}
	if sw.SourceTimeframe != "M15" {
		t.Error("SourceTimeframe mismatch")
	}
	if sw.ExcursionPrice != 4 {
		t.Error("ExcursionPrice mismatch")
	}
	if sw.ExcursionATR != 2.0 {
		t.Error("ExcursionATR mismatch")
	}
	if sw.BarsToConfirm != 2 {
		t.Error("BarsToConfirm mismatch")
	}
	if sw.ConfirmedAt != 7 {
		t.Error("ConfirmedAt mismatch")
	}
	if sw.ID == "" {
		t.Error("ID should be non-empty")
	}
}

func TestPromoteSwing_DifferentPivotsProduceDifferentIDs(t *testing.T) {
	a, _ := structure.PromoteSwing(structure.Pivot{Kind: structure.PivotHigh, Strength: 1, Time: 1, ConfirmedAt: 3}, "M5", 2, defaultPromotion())
	b, _ := structure.PromoteSwing(structure.Pivot{Kind: structure.PivotHigh, Strength: 1, Time: 2, ConfirmedAt: 4}, "M5", 2, defaultPromotion())
	if a.ID == b.ID {
		t.Errorf("two swings at different times must not collide on ID: both were %q", a.ID)
	}
}
