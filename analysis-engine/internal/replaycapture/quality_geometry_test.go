package replaycapture_test

// SQ-2 on the real EURUSD Trendline fixture of trendline_mtf_test.go: the quality models read the entry, the effective risk and the room; they change no trade.

import (
	"math"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

func TestTheQualityGeometryIsPublishedAndTheOverallIsTheMeanOfTheScores(t *testing.T) {
	s := load(t)
	got, _ := replayWithOptions(t, capture(map[string][][]float64{"M15": s.M15, "H1": s.H1}), market.M15, nil)
	o := only(t, got)
	c := o.Quality.Components
	if c[quality.ModelVersionKey] != 2 {
		t.Errorf("trendline quality model version %v, want 2", c[quality.ModelVersionKey])
	}
	sum, n := 0.0, 0
	for name, v := range c {
		if math.IsNaN(v) || math.IsInf(v, 0) {
			t.Errorf("%s = %v is not finite", name, v)
		}
		if strings.HasPrefix(name, quality.Prefix) {
			if v < 0 || v > 1 {
				t.Errorf("%s = %v outside [0, 1]", name, v)
			}
			sum += v
			n++
		}
	}
	if n != 8 || math.Abs(o.Quality.Overall-sum/float64(n)) > 1e-12 {
		t.Errorf("overall %v is not the mean %v of the %d dimensions", o.Quality.Overall, sum/float64(n), n)
	}
	for _, name := range []string{"structural_risk_pips", "effective_risk_pips", "effective_room_r", "room_structural", "available_room_pips", "target_room_pips", "entry_favourable_depth_atr"} {
		if _, ok := c[name]; !ok {
			t.Errorf("measurement %q is not published", name)
		}
	}
	// EURUSD's execution floor is the effective risk when the structure is tighter, and never less than the structural stop.
	if c["effective_risk_pips"] < c["structural_risk_pips"] {
		t.Errorf("effective risk %v pips is below the structural %v", c["effective_risk_pips"], c["structural_risk_pips"])
	}
	// The scored room is over the effective risk.
	if want := quality.Room(c["effective_room_r"], 1, o.Quality.Components["room_structural"] == 1); math.Abs(c["score_room"]-want) > 1e-9 {
		t.Errorf("score_room %v, want Room(effective %vR) = %v", c["score_room"], c["effective_room_r"], want)
	}
}

// Changing what a quality model reads must not change a trade: the same candidates, entries, stops, targets and evidence under any barrier policy.
func TestTheBarrierPolicyChangesQualityAndNothingElse(t *testing.T) {
	s := load(t)
	c := capture(map[string][][]float64{"M15": s.M15, "H1": s.H1})
	a, _ := replayWithOptions(t, c, market.M15, map[string]any{"barrier_buffer_atr": 0.0})
	b, _ := replayWithOptions(t, c, market.M15, map[string]any{"barrier_buffer_atr": 25.0, "barrier_max_width_atr": 50.0, "barrier_max_width_pips": 5000.0})
	if len(a) != len(b) || ids(a) != ids(b) {
		t.Fatalf("candidates changed with the barrier policy: %s vs %s", ids(a), ids(b))
	}
	for i := range a {
		x, y := a[i], b[i]
		if x.Entry != y.Entry || x.Invalidation != y.Invalidation || x.Direction != y.Direction || len(x.Targets) != len(y.Targets) ||
			x.Targets[0] != y.Targets[0] || x.FormedAt != y.FormedAt || x.ExpiresAt != y.ExpiresAt || len(x.Evidence) != len(y.Evidence) {
			t.Errorf("the trade contract of %s changed with a quality-only policy", x.ID)
		}
	}
}
