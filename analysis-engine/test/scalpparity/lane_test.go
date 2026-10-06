package scalpparity_test

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// goldenLane is one strategy's expectations on the bars of a capture.
type goldenLane struct {
	Cycles        []int64
	Opportunities []laneOpportunity
}

type laneOpportunity struct {
	Bar       int64   `json:"bar"`
	Direction string  `json:"direction"`
	ZoneLow   float64 `json:"zone_low"`
	ZoneHigh  float64 `json:"zone_high"`
	Invalid   float64 `json:"invalidation"`
	Target    float64 `json:"target"`
	Quality   float64 `json:"quality"` // 0–100, breakout only
}

func readJSON(t *testing.T, name string, into any) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, into); err != nil {
		t.Fatal(err)
	}
}

// replayLane replays a capture through the Go engine and returns the
// candidates of one strategy per closed M1 bar.
func replayLane(t *testing.T, capture, strategyID string) map[int64][]opportunity.Candidate {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	c, err := replaycapture.Load(filepath.Join("..", "..", "testdata", capture))
	if err != nil {
		t.Fatal(err)
	}
	got := map[int64][]opportunity.Candidate{}
	_, err = replaycapture.Replay(doc, c, market.M1, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M1 {
			return
		}
		for _, candidate := range e.Candidates {
			if string(candidate.Strategy) == strategyID {
				got[e.BarTime] = append(got[e.BarTime], candidate)
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return got
}

// compareLane requires the same opportunities on the same bars. checkQuality
// also requires the breakout quality score.
func compareLane(t *testing.T, label string, lane goldenLane, got map[int64][]opportunity.Candidate, tick float64, checkQuality bool) (matched int) {
	t.Helper()
	cycles := map[int64]bool{}
	for _, bar := range lane.Cycles {
		cycles[bar] = true
	}
	wantByBar := map[int64][]laneOpportunity{}
	for _, o := range lane.Opportunities {
		wantByBar[o.Bar] = append(wantByBar[o.Bar], o)
	}
	near := func(a, b float64) bool { return math.Abs(a-b) <= tick+1e-9 }
	bars := append([]int64(nil), lane.Cycles...)
	sort.Slice(bars, func(i, j int) bool { return bars[i] < bars[j] })
	for _, bar := range bars {
		expected, actual := wantByBar[bar], got[bar]
		if len(expected) != len(actual) {
			t.Errorf("%s bar %d: Python %d opportunities, Go %d", label, bar, len(expected), len(actual))
			continue
		}
		sort.Slice(expected, func(i, j int) bool { return expected[i].Direction < expected[j].Direction })
		sort.Slice(actual, func(i, j int) bool { return actual[i].Direction < actual[j].Direction })
		for i := range expected {
			e, a := expected[i], actual[i]
			if string(a.Direction) != e.Direction || !near(a.Entry.Low, e.ZoneLow) || !near(a.Entry.High, e.ZoneHigh) ||
				!near(float64(a.Invalidation.Price), e.Invalid) || !near(float64(a.Targets[0].Price.Price), e.Target) ||
				checkQuality && math.Abs(a.Quality.Overall*100-e.Quality) > 1e-6 {
				t.Errorf("%s bar %d %s: Python zone %.3f-%.3f stop %.3f target %.3f; Go zone %.3f-%.3f stop %.3f target %.3f",
					label, bar, e.Direction, e.ZoneLow, e.ZoneHigh, e.Invalid, e.Target,
					a.Entry.Low, a.Entry.High, float64(a.Invalidation.Price), float64(a.Targets[0].Price.Price))
				continue
			}
			matched++
		}
	}
	// The Python harness only evaluates a cycle once it has the full 120 M5 and 60
	// M1 bars; the Go lane also runs on the shorter warm-up windows before that,
	// which is not a decision the oracle made.
	first := int64(math.MaxInt64)
	for _, bar := range lane.Cycles {
		if bar < first {
			first = bar
		}
	}
	for bar := range got {
		if !cycles[bar] && bar >= first {
			t.Errorf("%s: Go produced an opportunity on bar %d that Python never evaluated", label, bar)
		}
	}
	return matched
}
