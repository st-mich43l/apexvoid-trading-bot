package replaycapture_test

import (
	"fmt"
	"math"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// TestQualityScoresAcrossStrategiesAreMeasuredNotAssumed measures, on every committed
// production capture, how Quality.Overall is distributed per strategy and what it does
// to the cross-strategy ranking the execution cycle applies (rank order: executable,
// quality, confluence, structural quality, freshness, intent id).
//
// It is a measurement with hard invariants, not a performance claim: quality is
// compared with other quality, never with outcomes (no realized outcome data exists for
// the captures). The distribution it logs is the evidence behind the finding in
// docs/strategies/independence-audit.md.
func TestQualityScoresAcrossStrategiesAreMeasuredNotAssumed(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	captures := []string{
		"replay-xau-m1-production-capture-20261006.json",
		"replay-xau-production-capture-20260921.json",
		"replay-eurusd-production-capture-20261006.json",
		"replay-gbpusd-production-capture-20261005.json",
		"replay-gbpjpy-production-capture-20261006.json",
		"replay-usdjpy-production-capture-20261005.json",
	}
	type cycle struct {
		direction market.Direction
		items     []opportunity.Candidate
	}
	results := make([][]cycle, len(captures))
	var wg sync.WaitGroup
	for i, name := range captures {
		wg.Add(1)
		go func(i int, name string) {
			defer wg.Done()
			capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
			if err != nil {
				t.Error(err)
				return
			}
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				for _, direction := range []market.Direction{market.Buy, market.Sell} {
					var items []opportunity.Candidate
					for _, c := range e.Candidates {
						if c.Reaction != nil && c.Direction == direction && c.Technical != nil {
							items = append(items, c)
						}
					}
					if len(items) > 0 {
						results[i] = append(results[i], cycle{direction, items})
					}
				}
			}})
			if err != nil {
				t.Error(err)
			}
		}(i, name)
	}
	wg.Wait()
	if t.Failed() {
		return
	}

	// 1. Per-strategy distribution of confirmed candidates (all captures pooled).
	scores := map[string][]float64{}
	for _, cycles := range results {
		for _, c := range cycles {
			for _, item := range c.items {
				if item.Quality.Overall < 0 || item.Quality.Overall > 1 || math.IsNaN(item.Quality.Overall) {
					t.Fatalf("%s quality %v outside [0,1]", item.Strategy, item.Quality.Overall)
				}
				scores[string(item.Strategy)] = append(scores[string(item.Strategy)], item.Quality.Overall)
			}
		}
	}
	var names []string
	for name := range scores {
		names = append(names, name)
	}
	sort.Strings(names)
	distinct := func(v []float64) int {
		seen := map[int]bool{}
		for _, x := range v {
			seen[int(math.Round(x*100))] = true
		}
		return len(seen)
	}
	pct := func(v []float64, p float64) float64 {
		s := append([]float64(nil), v...)
		sort.Float64s(s)
		return s[int(p*float64(len(s)-1))]
	}
	var lines []string
	for _, name := range names {
		v := scores[name]
		lines = append(lines, fmt.Sprintf("%-22s n=%5d distinct(1%%)=%2d min %.2f p50 %.2f p90 %.2f max %.2f",
			name, len(v), distinct(v), pct(v, 0), pct(v, .5), pct(v, .9), pct(v, 1)))
	}
	t.Logf("confirmed-candidate quality per strategy, six captures pooled:\n%s", strings.Join(lines, "\n"))

	// 2. Competing groups: same symbol, same direction, same closed bar, entry corridors
	// overlapping after a 1 ATR pad (the entry-overlap corridor), at least two strategies.
	// percentile = mid-rank of the score inside its own strategy's pooled distribution.
	percentile := func(strategy string, q float64) float64 {
		v := scores[strategy]
		below, equal := 0, 0
		for _, x := range v {
			switch {
			case x < q-1e-9:
				below++
			case math.Abs(x-q) <= 1e-9:
				equal++
			}
		}
		return (float64(below) + float64(equal)/2) / float64(len(v))
	}
	var groups, tiedTop, rawVsPercentile, decidedByContinuous int
	winsRaw := map[string]int{}
	winsPct := map[string]int{}
	continuous := map[string]bool{}
	for name, v := range scores {
		continuous[name] = distinct(v) > 3
	}
	for _, cycles := range results {
		for _, c := range cycles {
			n := len(c.items)
			parent := make([]int, n)
			for i := range parent {
				parent[i] = i
			}
			var find func(int) int
			find = func(x int) int {
				for parent[x] != x {
					parent[x] = parent[parent[x]]
					x = parent[x]
				}
				return x
			}
			for i := 0; i < n; i++ {
				for j := i + 1; j < n; j++ {
					pad := c.items[i].Technical.ATR
					if c.items[i].Entry.Low-pad <= c.items[j].Entry.High && c.items[i].Entry.High+pad >= c.items[j].Entry.Low {
						parent[find(i)] = find(j)
					}
				}
			}
			members := map[int][]int{}
			for i := 0; i < n; i++ {
				members[find(i)] = append(members[find(i)], i)
			}
			for _, idx := range members {
				strategies := map[string]bool{}
				for _, i := range idx {
					strategies[string(c.items[i].Strategy)] = true
				}
				if len(strategies) < 2 {
					continue
				}
				groups++
				best, bestPct := idx[0], idx[0]
				for _, i := range idx[1:] {
					if c.items[i].Quality.Overall > c.items[best].Quality.Overall+1e-9 {
						best = i
					}
					a, b := c.items[i], c.items[bestPct]
					if percentile(string(a.Strategy), a.Quality.Overall) > percentile(string(b.Strategy), b.Quality.Overall)+1e-9 {
						bestPct = i
					}
				}
				top := 0
				for _, i := range idx {
					if math.Abs(c.items[i].Quality.Overall-c.items[best].Quality.Overall) <= 1e-9 {
						top++
					}
				}
				if top > 1 {
					tiedTop++
				} else {
					winsRaw[string(c.items[best].Strategy)]++
					if continuous[string(c.items[best].Strategy)] {
						decidedByContinuous++
					}
					if c.items[best].Strategy != c.items[bestPct].Strategy {
						rawVsPercentile++
					}
					winsPct[string(c.items[bestPct].Strategy)]++
				}
			}
		}
	}
	if groups == 0 {
		t.Fatal("no competing groups in the captures, so the cross-strategy ranking was not exercised")
	}
	decided := groups - tiedTop
	var winners []string
	for name := range winsRaw {
		winners = append(winners, name)
	}
	sort.Strings(winners)
	var winLines []string
	for _, name := range winners {
		winLines = append(winLines, fmt.Sprintf("%-22s decided by quality %4d  (by within-strategy percentile %4d)", name, winsRaw[name], winsPct[name]))
	}
	t.Logf("%d competing groups (>=2 strategies on one corridor): %d (%.0f%%) tie at the top quality and fall through to confluence / structural quality / freshness / intent id; %d decided on quality alone, %d of those won by a strategy whose scale is continuous, %d change winner under within-strategy percentile normalisation\n%s",
		groups, tiedTop, 100*float64(tiedTop)/float64(groups), decided, decidedByContinuous, rawVsPercentile, strings.Join(winLines, "\n"))
}
