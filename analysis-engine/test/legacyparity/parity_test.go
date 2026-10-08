package legacyparity_test

import (
	"encoding/json"
	"fmt"
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

// The golden is the decision of the frozen Python detectors that the Go
// session_level, flip_zone, trendline and box_breakout strategies replaced, over
// the committed closed-bar captures (generate_oracle_golden.py). The Go
// strategies must agree bar by bar on presence, direction, entry band and
// confluence stars: none may fire where the oracle did not, none may stay
// silent where it did. Detectors listed in measuredOnly are compared and
// reported but do not fail the test: they are the strategies whose parity has
// not been restored yet, kept visible rather than silently skipped.

const frozenCommit = "1c9f32303b7e9f15fc8e3767b5045ac1e8a37adc"

// pairing maps each frozen detector to the Go strategy that replaced it and the
// direction filter (flip zones are one Go strategy over two detectors).
var pairing = map[string]struct {
	strategy  opportunity.StrategyID
	direction market.Direction
}{
	"session_level_reaction":    {"session_level", ""},
	"flip_demand_zone_reaction": {"flip_zone", market.Buy},
	"flip_supply_zone_reaction": {"flip_zone", market.Sell},
	"trendline_reaction":        {"trendline", ""},
	"box_breakout":              {"box_breakout", ""},
}

// measuredOnly lists the detectors whose parity is reported but not asserted; it is
// empty when every replaced detector is proven.
var measuredOnly = map[string]bool{}

type goldenDecision struct {
	Direction       string  `json:"direction"`
	EntryLow        float64 `json:"entry_low"`
	EntryHigh       float64 `json:"entry_high"`
	Confluence      int     `json:"confluence"`
	TouchBar        *int64  `json:"touch_bar"`
	ConfirmationBar *int64  `json:"confirmation_bar"`
}

type goldenSymbol struct {
	Capture  string `json:"capture"`
	FirstBar int64  `json:"first_bar"`
	LastBar  int64  `json:"last_bar"`
	Bars     []struct {
		Bar       int64                     `json:"bar"`
		Detectors map[string]goldenDecision `json:"detectors"`
	} `json:"bars"`
}

type golden struct {
	OracleCommit string                  `json:"oracle_commit"`
	Symbols      map[string]goldenSymbol `json:"symbols"`
}

// outwardTick is the engine's price normalisation of an entry band: low floored
// and high ceiled onto the instrument's tick grid.
func outwardTick(low, high float64, digits int) (float64, float64) {
	tick := math.Pow10(-digits)
	return math.Floor(low/tick+1e-6) * tick, math.Ceil(high/tick-1e-6) * tick
}

func observe(t *testing.T, doc *config.Document, capture string) map[int64]map[opportunity.StrategyID][]opportunity.Candidate {
	t.Helper()
	loaded, err := replaycapture.Load(filepath.Join("..", "..", "testdata", capture))
	if err != nil {
		t.Fatal(err)
	}
	got := map[int64]map[opportunity.StrategyID][]opportunity.Candidate{}
	_, err = replaycapture.Replay(doc, loaded, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 {
			return
		}
		bar := map[opportunity.StrategyID][]opportunity.Candidate{}
		for _, c := range e.Candidates {
			bar[c.Strategy] = append(bar[c.Strategy], c)
		}
		got[e.BarTime] = bar
	}})
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func TestLegacyStrategiesMatchTheFrozenPythonOracleBarByBar(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "testdata", "legacy-strategies-oracle.json"))
	if err != nil {
		t.Fatal(err)
	}
	var g golden
	if err := json.Unmarshal(data, &g); err != nil {
		t.Fatal(err)
	}
	if g.OracleCommit != frozenCommit {
		t.Fatalf("golden was not generated from the frozen oracle: %q", g.OracleCommit)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var symbols, names []string
	for symbol := range g.Symbols {
		symbols = append(symbols, symbol)
	}
	for name := range pairing {
		names = append(names, name)
	}
	sort.Strings(symbols)
	sort.Strings(names)
	type tally struct{ oracle, goFire, matched int }
	tallies := map[string]*tally{}
	for _, name := range names {
		tallies[name] = &tally{}
	}
	for _, symbol := range symbols {
		want := g.Symbols[symbol]
		geometry, err := doc.GeometryFor(symbol)
		if err != nil {
			t.Fatal(err)
		}
		got := observe(t, doc, want.Capture)
		expected := map[int64]map[string]goldenDecision{}
		for _, bar := range want.Bars {
			expected[bar.Bar] = bar.Detectors
		}
		barSet := map[int64]bool{}
		for bar := range got {
			if bar >= want.FirstBar && bar <= want.LastBar {
				barSet[bar] = true
			}
		}
		for bar := range expected {
			barSet[bar] = true
		}
		var bars []int64
		for bar := range barSet {
			bars = append(bars, bar)
		}
		sort.Slice(bars, func(i, j int) bool { return bars[i] < bars[j] })
		failures := map[string][]string{}
		for _, bar := range bars {
			for _, name := range names {
				pair := pairing[name]
				var emitted []opportunity.Candidate
				for _, c := range got[bar][pair.strategy] {
					if c.Reaction == nil && name != "box_breakout" {
						continue
					}
					if pair.direction == "" || c.Direction == pair.direction {
						emitted = append(emitted, c)
					}
				}
				oracle, wantFire := expected[bar][name]
				tl := tallies[name]
				if wantFire {
					tl.oracle++
				}
				if len(emitted) > 0 {
					tl.goFire++
				}
				fail := func(format string, args ...any) {
					failures[name] = append(failures[name], fmt.Sprintf("%s %s bar %d: %s", symbol, name, bar, fmt.Sprintf(format, args...)))
				}
				switch {
				case wantFire && len(emitted) == 0:
					fail("oracle %s, Go silent", oracle.Direction)
				case !wantFire && len(emitted) > 0:
					fail("Go %v, oracle silent", directions(emitted))
				case wantFire:
					var match *opportunity.Candidate
					for i := range emitted {
						if string(emitted[i].Direction) == oracle.Direction {
							match = &emitted[i]
							break
						}
					}
					if match == nil {
						fail("direction %v, oracle %s", directions(emitted), oracle.Direction)
						continue
					}
					low, high := outwardTick(oracle.EntryLow, oracle.EntryHigh, geometry.PriceDigits)
					tolerance := math.Pow10(-geometry.PriceDigits) * .01
					if math.Abs(float64(match.Entry.Low)-low) > tolerance || math.Abs(float64(match.Entry.High)-high) > tolerance {
						fail("entry %.6f-%.6f, oracle %.6f-%.6f", match.Entry.Low, match.Entry.High, low, high)
						continue
					}
					stars := -1
					if match.Technical != nil && match.Technical.Confluence != nil {
						stars = match.Technical.Confluence.SelectedStars
					}
					if stars != oracle.Confluence {
						fail("confluence %d, oracle %d", stars, oracle.Confluence)
						continue
					}
					tl.matched++
				}
			}
		}
		for _, name := range names {
			list := failures[name]
			if len(list) == 0 {
				continue
			}
			for i, m := range list {
				if i == 8 {
					t.Logf("%s: ... and %d more", name, len(list)-8)
					break
				}
				if measuredOnly[name] {
					t.Logf("not yet restored: %s", m)
				} else {
					t.Error(m)
				}
			}
		}
	}
	for _, name := range names {
		tl := tallies[name]
		status := "PROVEN"
		if measuredOnly[name] {
			status = "measured only"
		}
		t.Logf("%-28s oracle %4d  go %4d  identical %4d  [%s]", name, tl.oracle, tl.goFire, tl.matched, status)
		if !measuredOnly[name] && tl.oracle == 0 {
			t.Errorf("the golden holds no %s decision to compare", name)
		}
	}
}

func directions(candidates []opportunity.Candidate) []string {
	var out []string
	for _, c := range candidates {
		out = append(out, string(c.Direction))
	}
	return out
}
