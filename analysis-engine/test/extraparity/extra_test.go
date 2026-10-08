package extraparity_test

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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

type det struct {
	Direction string  `json:"direction"`
	EntryLow  float64 `json:"entry_low"`
	EntryHigh float64 `json:"entry_high"`
	Confl     int     `json:"confluence"`
}
type bar struct {
	Bar       int64          `json:"bar"`
	Detectors map[string]det `json:"detectors"`
}
type sym struct {
	Capture string `json:"capture"`
	Bars    []bar  `json:"bars"`
}

var mapping = map[string]string{"flip_demand": "flip_zone", "flip_supply": "flip_zone", "session_level": "session_level", "trendline": "trendline", "box_breakout": "box_breakout"}

func TestExtra(t *testing.T) {
	raw, err := os.ReadFile("/tmp/verify-out/extra-oracle.json")
	if err != nil {
		t.Fatal(err)
	}
	var g struct{ Symbols map[string]sym }
	if err := json.Unmarshal(raw, &g); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var symbols []string
	for s := range g.Symbols {
		symbols = append(symbols, s)
	}
	sort.Strings(symbols)
	for _, s := range symbols {
		data := g.Symbols[s]
		capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", data.Capture))
		if err != nil {
			t.Fatal(err)
		}
		type gc struct {
			dir       string
			lo, hi    float64
			confirmed bool
		}
		got := map[int64]map[string][]gc{}
		_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
			if e.Timeframe != market.M5 {
				return
			}
			m := map[string][]gc{}
			for _, c := range e.Candidates {
				m[string(c.Strategy)] = append(m[string(c.Strategy)], gc{string(c.Direction), float64(c.Entry.Low), float64(c.Entry.High), c.Reaction != nil})
			}
			got[e.BarTime] = m
		}})
		if err != nil {
			t.Fatal(err)
		}
		type stat struct{ oracle, goConfirmed, both, onlyOracle, onlyGo, entryMatch, dirMismatch int }
		stats := map[string]*stat{}
		for _, b := range data.Bars {
			for name := range map[string]bool{"flip_demand": true, "flip_supply": true, "session_level": true, "trendline": true, "box_breakout": true} {
				st := stats[name]
				if st == nil {
					st = &stat{}
					stats[name] = st
				}
				want, has := b.Detectors[name]
				var cands []gc
				for _, c := range got[b.Bar][mapping[name]] {
					if c.confirmed || name == "box_breakout" {
						want2 := ""
						switch name {
						case "flip_demand":
							want2 = "BUY"
						case "flip_supply":
							want2 = "SELL"
						}
						if want2 == "" || c.dir == want2 {
							cands = append(cands, c)
						}
					}
				}
				if has {
					st.oracle++
				}
				if len(cands) > 0 {
					st.goConfirmed++
				}
				switch {
				case has && len(cands) > 0:
					st.both++
					ok := false
					dirOK := false
					for _, c := range cands {
						if c.dir == want.Direction {
							dirOK = true
							if math.Abs(c.lo-want.EntryLow) < 0.02*math.Max(1, math.Abs(want.EntryLow)*1e-3) && math.Abs(c.hi-want.EntryHigh) < 0.02*math.Max(1, math.Abs(want.EntryHigh)*1e-3) {
								ok = true
							}
						}
					}
					if ok {
						st.entryMatch++
					}
					if !dirOK {
						st.dirMismatch++
					}
				case has:
					st.onlyOracle++
				case len(cands) > 0:
					st.onlyGo++
				}
			}
		}
		var names []string
		for n := range stats {
			names = append(names, n)
		}
		sort.Strings(names)
		for _, n := range names {
			st := stats[n]
			fmt.Printf("%-7s %-13s oracle=%4d go=%4d both=%4d onlyOracle=%4d onlyGo=%4d entryMatch=%4d dirMismatch=%d\n", s, n, st.oracle, st.goConfirmed, st.both, st.onlyOracle, st.onlyGo, st.entryMatch, st.dirMismatch)
		}
	}
}
