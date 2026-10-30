package supplyv7_test

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// Both versions on the same engine contexts over every committed capture: what v7 publishes that v6 did not, what it no longer publishes, and why
// (the refusal histogram). The counts are PUBLISHES, not profit: no capture here knows what a published setup went on to do.
//
// The vendored v6 must still be the v6 master ran. Commit A of this change proved the self-contained v6 package identical to master on 24 captures
// (45,723 closed M5 bars, 6,024 confirmed candidates); on the same captures the vendored v6, now on an engine that no longer carries the supply zone,
// must publish exactly that many. SUPPLY_V7_REPORT=<file> writes the tables of the pull request.
// setupOf identifies a published setup across versions: the structure and the rejection bar (the opportunity id carries the version, and the touch bar can
// differ between the versions for the same rejection).
func setupOf(c opportunity.Candidate) string {
	return fmt.Sprintf("%s|%d", c.StructuralID, c.Reaction.ConfirmationBarTime)
}

func TestV6ToV7Measurement(t *testing.T) {
	const wantBars, wantConfirmed = 45723, 6024
	type setups map[string]opportunity.Candidate
	v6, v7 := map[string]setups{}, map[string]setups{}
	refusals := map[string]int64{}
	refusalsByTF := map[string]int64{}
	bars, v6Evaluations := 0, 0
	key := func(path string) string { return strings.TrimSuffix(filepath.Base(path), ".json") }
	walkAll(t, func(path string, b bar, e engine.Evaluation) {
		k := key(path)
		if v6[k] == nil {
			v6[k], v7[k] = setups{}, setups{}
		}
		bars++
		v6Evaluations += len(b.v6)
		for _, c := range b.v6 {
			v6[k][setupOf(c)] = c
		}
		for _, c := range b.v7 {
			v7[k][setupOf(c)] = c
		}
		for r, n := range b.refusals {
			refusals[r.Reason] += n
			refusalsByTF[string(r.Timeframe)+"/"+r.Reason] += n
		}
	})
	if bars != wantBars || v6Evaluations != wantConfirmed {
		t.Fatalf("the vendored v6 is not master's: %d bars / %d confirmed candidates, commit A proved %d / %d", bars, v6Evaluations, wantBars, wantConfirmed)
	}

	var out strings.Builder
	p := func(format string, args ...any) { fmt.Fprintf(&out, format+"\n", args...) }
	names := make([]string, 0, len(v6))
	for k := range v6 {
		names = append(names, k)
	}
	sort.Strings(names)
	p("| capture | v6 setups | v7 setups | in both | v6 only | v7 only |\n|---|---:|---:|---:|---:|---:|")
	var t6, t7, tBoth int
	for _, k := range names {
		both := 0
		for s := range v7[k] {
			if _, ok := v6[k][s]; ok {
				both++
			}
		}
		t6, t7, tBoth = t6+len(v6[k]), t7+len(v7[k]), tBoth+both
		p("| %s | %d | %d | %d | %d | %d |", k, len(v6[k]), len(v7[k]), both, len(v6[k])-both, len(v7[k])-both)
	}
	p("| **total** | %d | %d | %d | %d | %d |\n", t6, t7, tBoth, t6-tBoth, t7-tBoth)

	p("setups per symbol and structure timeframe (v6 -> v7):\n| symbol | M5 | M15 | H1 |\n|---|---:|---:|---:|")
	symbols := map[string]map[string][2]int{}
	for _, k := range names {
		sym := strings.Split(k, "-")[1]
		if symbols[sym] == nil {
			symbols[sym] = map[string][2]int{}
		}
		for _, c := range v6[k] {
			n := symbols[sym][string(c.Structure.Timeframe)]
			n[0]++
			symbols[sym][string(c.Structure.Timeframe)] = n
		}
		for _, c := range v7[k] {
			n := symbols[sym][string(c.Structure.Timeframe)]
			n[1]++
			symbols[sym][string(c.Structure.Timeframe)] = n
		}
	}
	syms := make([]string, 0, len(symbols))
	for sym := range symbols {
		syms = append(syms, sym)
	}
	sort.Strings(syms)
	for _, sym := range syms {
		m := symbols[sym]
		p("| %s | %d -> %d | %d -> %d | %d -> %d |", sym, m["M5"][0], m["M5"][1], m["M15"][0], m["M15"][1], m["H1"][0], m["H1"][1])
	}
	p("\nsetups by the timeframe their zone lives on:\n| timeframe | v6 | v6 also published by v7 | v7 |\n|---|---:|---:|---:|")
	for _, tf := range []string{"M5", "M15", "H1"} {
		n6, nBoth, n7 := 0, 0, 0
		for _, k := range names {
			for s, c := range v6[k] {
				if string(c.Structure.Timeframe) == tf {
					n6++
					if _, ok := v7[k][s]; ok {
						nBoth++
					}
				}
			}
			for _, c := range v7[k] {
				if string(c.Structure.Timeframe) == tf {
					n7++
				}
			}
		}
		p("| %s | %d | %d | %d |", tf, n6, nBoth, n7)
	}
	p("\nrefusals counted by v7, per evaluated M5 bar (a structure that stays refused is counted again on every bar it is in play):")
	reasons := make([]string, 0, len(refusalsByTF))
	for r := range refusalsByTF {
		reasons = append(reasons, r)
	}
	sort.Strings(reasons)
	p("| structure timeframe / reason | count |\n|---|---:|")
	for _, r := range reasons {
		p("| %s | %d |", r, refusalsByTF[r])
	}

	p("\nv7 published setups by room_r (room to the target in units of the risk) and by the zone's touches:")
	roomBins := []struct {
		name   string
		lo, hi float64
	}{{"[1, 1.5)", 1, 1.5}, {"[1.5, 2)", 1.5, 2}, {"[2, inf)", 2, math.Inf(1)}}
	touchBins := []struct {
		name   string
		lo, hi float64
	}{{"0", 0, 1}, {"1", 1, 2}, {"2", 2, math.Inf(1)}}
	roomCount, touchCount := make([]int, len(roomBins)), make([]int, len(touchBins))
	below := 0
	for _, k := range names {
		for _, c := range v7[k] {
			room, touches := c.Quality.Components["room_r"], c.Quality.Components["zone_touches"]
			placed := false
			for i, bin := range roomBins {
				if room >= bin.lo && room < bin.hi {
					roomCount[i]++
					placed = true
				}
			}
			if !placed {
				below++
			}
			for i, bin := range touchBins {
				if touches >= bin.lo && touches < bin.hi {
					touchCount[i]++
				}
			}
		}
	}
	p("| room_r | setups |\n|---|---:|")
	for i, bin := range roomBins {
		p("| %s | %d |", bin.name, roomCount[i])
	}
	p("| under 1 | %d |", below)
	p("\n| touches | setups |\n|---|---:|")
	for i, bin := range touchBins {
		p("| %s | %d |", bin.name, touchCount[i])
	}
	if below != 0 {
		t.Errorf("%d published v7 setups have less than minimum_target_r of room", below)
	}
	if t7 == 0 {
		t.Fatal("v7 published nothing on any capture: the measurement proves nothing")
	}
	t.Log("\n" + out.String())
	if path := os.Getenv("SUPPLY_V7_REPORT"); path != "" {
		if err := os.WriteFile(path, []byte(out.String()), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}
