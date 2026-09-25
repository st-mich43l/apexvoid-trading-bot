package shadowcompare_test

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/shadowcompare"
	"testing"
)

func observation(engine, id string, at int64) shadowcompare.Observation {
	return shadowcompare.Observation{Engine: engine, ID: id, Symbol: "XAU", Timeframe: "M5", Strategy: "fvg", Direction: "BUY", InputFingerprint: "bars", ConfigFingerprint: "cfg", ConfirmedAt: at, Entry: shadowcompare.PriceBand{Low: 99, High: 100}, Invalidation: 98, Targets: []float64{102}, StructureFingerprint: "s", ZoneFingerprint: "z", Outcome: "target", MFE: 3, MAE: 1}
}
func TestSemanticMatchAndClassifications(t *testing.T) {
	p := observation("python", "p", 100)
	g := observation("go", "g", 120)
	g.ConfigFingerprint = "other"
	g.Entry = shadowcompare.PriceBand{Low: 101, High: 102}
	r := shadowcompare.Compare([]shadowcompare.Observation{p}, []shadowcompare.Observation{g}, 30, nil)
	if r.Matched != 1 || len(r.Differences) != 1 || r.Differences[0].Category != "configuration_difference" {
		t.Fatalf("unexpected report: %+v", r)
	}
}
func TestUnmatchedDoesNotBecomeFalseMatch(t *testing.T) {
	p := observation("python", "p", 100)
	g := observation("go", "g", 1000)
	r := shadowcompare.Compare([]shadowcompare.Observation{p}, []shadowcompare.Observation{g}, 30, nil)
	if r.Matched != 0 || r.PythonOnly != 1 || r.GoOnly != 1 {
		t.Fatalf("unexpected report: %+v", r)
	}
}
