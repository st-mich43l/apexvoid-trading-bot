package engine

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// A higher timeframe exports the techniques the zone strategies read (supply/demand, order block, fair value gap) and nothing else; every exported
// instance carries the timeframe it was found on.
func TestHigherTimeframeExportKeepsOnlyTheZoneStrategiesTechniques(t *testing.T) {
	all := []techniquezone.Instance{
		{Technique: "supply_demand", Side: "sell"},
		{Technique: "order_block", Side: "buy"},
		{Technique: "fvg", Side: "buy"},
		{Technique: "crt", Side: "buy"},
		{Technique: "ifvg", Side: "sell"},
		{Technique: "confluence_zone", Side: "sell"},
	}
	got := exportHigherTimeframe(all, market.H1)
	want := []string{"supply_demand", "order_block", "fvg"}
	if len(got) != len(want) {
		t.Fatalf("exported %d instances, want %d: %+v", len(got), len(want), got)
	}
	for i, in := range got {
		if in.Technique != want[i] || in.Timeframe != "H1" {
			t.Errorf("instance %d: technique %q timeframe %q, want %q H1", i, in.Technique, in.Timeframe, want[i])
		}
	}
	if all[0].Timeframe != "" {
		t.Error("the input instances must not be modified")
	}
	if exportHigherTimeframe(nil, market.M15) != nil {
		t.Error("nothing in, nothing out")
	}
}
