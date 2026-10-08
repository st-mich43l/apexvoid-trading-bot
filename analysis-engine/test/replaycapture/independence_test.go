package replaycapture_test

import (
	"encoding/json"
	"path/filepath"
	"sort"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// TestDisablingOneStrategyNeverChangesAnotherStrategysCandidates proves strategy
// independence on the real production capture: the full candidate (identity, entry band,
// stop, targets, quality, evidence — everything the JSON carries) of every other
// strategy is byte-identical per bar whether or not a given strategy is enabled.
// Confluence Zone, FVG, Order Block, Supply, Demand, Key Level and Flip Zone are the
// strategies that read the same canonical zone facts, so they are the ones a hidden
// coupling would show up between.
func TestDisablingOneStrategyNeverChangesAnotherStrategysCandidates(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", "replay-xau-m1-production-capture-20261006.json"))
	if err != nil {
		t.Fatal(err)
	}
	// candidates maps strategy -> set of "bar|timeframe|candidate JSON".
	run := func(disabled ...string) map[string]map[string]struct{} {
		out := map[string]map[string]struct{}{}
		_, err := replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{
			DisabledStrategies: disabled,
			OnEvaluation: func(e engine.Evaluation) {
				for _, c := range e.Candidates {
					raw, err := json.Marshal(c)
					if err != nil {
						t.Error(err)
						return
					}
					id := string(c.Strategy)
					if out[id] == nil {
						out[id] = map[string]struct{}{}
					}
					out[id][string(e.Timeframe)+"|"+itoa(e.BarTime)+"|"+string(raw)] = struct{}{}
				}
			},
		})
		if err != nil {
			t.Error(err)
		}
		return out
	}
	disable := []string{"confluence_zone", "fvg", "order_block", "supply", "demand", "key_level", "flip_zone"}
	results := make([]map[string]map[string]struct{}, len(disable)+1)
	var wg sync.WaitGroup
	for i := range results {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if i == len(disable) {
				results[i] = run()
				return
			}
			results[i] = run(disable[i])
		}(i)
	}
	wg.Wait()
	if t.Failed() {
		return
	}
	full := results[len(disable)]
	if len(full) < 8 {
		t.Fatalf("the full replay produced candidates from only %d strategies", len(full))
	}
	for i, off := range disable {
		without := results[i]
		if len(without[off]) != 0 {
			t.Errorf("%s was disabled yet still produced %d candidates", off, len(without[off]))
		}
		var ids []string
		for id := range full {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		for _, id := range ids {
			if id == off {
				continue
			}
			if len(full[id]) != len(without[id]) {
				t.Errorf("disabling %s changed the %s candidate count: %d -> %d", off, id, len(full[id]), len(without[id]))
				continue
			}
			for key := range full[id] {
				if _, ok := without[id][key]; !ok {
					t.Errorf("disabling %s changed a %s candidate: %.200s", off, id, key)
					break
				}
			}
		}
		t.Logf("without %-16s the other %d strategies are unchanged (%d candidates removed with it)", off, len(full)-1, len(full[off]))
	}
}
