package roundnumber

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

func TestQualityComponentsAreInRangeAndOverallIsTheirMean(t *testing.T) {
	bars, _ := bullishStopRun()
	got := newStrategy(t, nil).Evaluate(marketContext(bars, "up"))
	if len(got) != 1 {
		t.Fatalf("candidates = %d", len(got))
	}
	if err := quality.Verify(got[0].Quality.Overall, got[0].Quality.Components); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"force", "rejection", "sweep_depth", "entry_location", "structure", "room", "major_handle"} {
		if _, ok := got[0].Quality.Components[quality.Prefix+name]; !ok {
			t.Errorf("dimension %q is not published", name)
		}
	}
	if got[0].Quality.Components[quality.Prefix+"major_handle"] != 0 {
		t.Error("4110 is not a major handle (a multiple of 50)")
	}
}
