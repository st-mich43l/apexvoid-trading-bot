// Package pdmeasure_test measures what the owned zone score and the corrected dealing range do to supply (v8 -> v9) and demand (v6 -> v7): published counts,
// how often the premium/discount points were awarded by trend state under the old and the new reading, the stars, and the XAU SELL/BUY mix. It publishes
// counts, not profit. PD_REPORT=<file> writes the tables.
package pdmeasure_test

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/demand"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/supply"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/demandv7/reference"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/pdmeasure/supplyv8"
	supplyref "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/supplyv7/reference"
)

func captures() []string {
	var out []string
	for _, pattern := range []string{"replay-*-production-capture-*.json", "obwindow-*.json", "fvgwindow-*.json"} {
		matches, _ := filepath.Glob(filepath.Join("..", "..", "testdata", pattern))
		for _, m := range matches {
			if !strings.Contains(m, "-m1-") {
				out = append(out, m)
			}
		}
	}
	sort.Strings(out)
	return out
}

func masterDemandParameters(settings engine.Settings) map[string]any {
	params := map[string]any{}
	for k, v := range settings.LegacyDetector {
		params[k] = v
	}
	for k, v := range map[string]any{
		"maximum_zone_touches": 2.0, "minimum_strength": 0.4, "invalidation_buffer_atr": 0.5,
		"minimum_target_distance_atr": 1.0, "expiry_hours": 24.0, "reaction_lookback_bars": 3.0, "engulfing_minimum_range_atr": 0.5, "epsilon_atr_fraction": 0.05,
		"maximum_zone_atr": 3.0, "entry_max_width_price": 5.0, "higher_timeframes": []any{"M15", "H1"},
		"pip_size": settings.LegacyRead.PipSize, "fvg_entry_max_width_price": settings.TechniqueZones.Technique.FVGEntryMaxWidthPrice,
		"confluence_scoring_version": settings.Confluence.ScoringVersion, "confluence_star_three_ratio": settings.Confluence.StarThreeRatio,
		"confluence_star_two_ratio": settings.Confluence.StarTwoRatio, "confluence_zone_quality_weight": settings.Confluence.ZoneQualityWeight,
		"confluence_mad_score_weight": settings.Confluence.MADScoreWeight, "fibonacci_confluence_weight": settings.Confluence.FibonacciWeight,
		"fibonacci_epsilon_atr": settings.Fib.EpsilonATR, "price_digits": float64(settings.Geometry.PriceDigits),
		"technique_retest_max_touches": float64(settings.TechniqueZones.Technique.RetestMaxTouches),
	} {
		params[k] = v
	}
	return params
}

// variants are the strategies compared on the same engine contexts.
type variants map[string]strategy.Strategy

func build(t testing.TB, doc *config.Document, symbol market.Symbol) variants {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(symbol)); err != nil {
		t.Fatal(err)
	}
	cfgOf := func(id strategy.StrategyID) strategy.Config {
		for _, c := range settings.Strategies {
			if c.ID == id {
				return c
			}
		}
		t.Fatalf("no %s", id)
		return strategy.Config{}
	}
	with := func(c strategy.Config, key string, value any) strategy.Config {
		params := map[string]any{}
		for k, v := range c.Parameters {
			params[k] = v
		}
		params[key] = value
		c.Parameters = params
		return c
	}
	must := func(s strategy.Strategy, err error) strategy.Strategy {
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	out := variants{}
	sup, dem := cfgOf("supply"), cfgOf("demand")
	out["supply_v8"] = must(supplyv8.New(sup))
	out["supply_v9_pd0"] = must(supply.New(with(sup, "pd_position_score", 0.0)))
	out["supply_v9"] = must(supply.New(sup))
	old := dem
	old.Version = "v6"
	old.Parameters = masterDemandParameters(settings)
	for k, v := range dem.Parameters {
		switch k {
		case "pip_size", "execution_stop_max_pips", "execution_stop_min_pips", "execution_pip_size":
			old.Parameters[k] = v
		case "entry_max_width_price":
			old.Parameters["entry_max_width_price"] = v
			old.Parameters["fvg_entry_max_width_price"] = v
		}
	}
	out["demand_v6"] = must(reference.New(old))
	oldSupply := sup
	oldSupply.Version = "v6"
	oldSupply.Parameters = masterDemandParameters(settings) // the same shared detector parameters, supply's own keys are identical
	for k, v := range sup.Parameters {
		switch k {
		case "pip_size", "execution_stop_max_pips", "execution_stop_min_pips", "execution_pip_size":
			oldSupply.Parameters[k] = v
		case "entry_max_width_price":
			oldSupply.Parameters["entry_max_width_price"] = v
			oldSupply.Parameters["fvg_entry_max_width_price"] = v
		}
	}
	out["supply_v6"] = must(supplyref.New(oldSupply))
	out["demand_v7_pd0"] = must(demand.New(with(dem, "pd_position_score", 0.0)))
	out["demand_v7"] = must(demand.New(dem))
	return out
}

type setup struct {
	side, key, symbol string
	tf                market.Timeframe
	stars             int
	roomR, touches    float64
	awarded           bool    // the zone earned the premium (supply) or discount (demand) points
	oldAwarded        bool    // what the shared reading (the frame's fib dealing range around price) would have awarded this zone
	trend             string  // the zone frame's market structure at the decision bar
	position          float64 // pd_position
	bar               int64   // the closed M5 bar the setup was published on
}

func confirmed(cs []opportunity.Candidate) []opportunity.Candidate {
	var out []opportunity.Candidate
	for _, c := range cs {
		if c.Reaction != nil {
			out = append(out, c)
		}
	}
	return out
}

type run struct {
	symbol  market.Symbol
	sets    map[string]map[string]setup // variant -> key -> setup
	refusal map[string]map[string]int64 // variant -> timeframe/reason -> count
}

func replay(t testing.TB, doc *config.Document, path string) run {
	t.Helper()
	capture, err := replaycapture.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	vs := build(t, doc, capture.Symbol)
	r := run{symbol: capture.Symbol, sets: map[string]map[string]setup{}, refusal: map[string]map[string]int64{}}
	for name := range vs {
		r.sets[name], r.refusal[name] = map[string]setup{}, map[string]int64{}
	}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		for name, s := range vs {
			for _, c := range confirmed(s.Evaluate(e.Context)) {
				side := "SELL"
				if c.Direction == market.Buy {
					side = "BUY"
				}
				su := setup{side: side, symbol: string(capture.Symbol), bar: e.BarTime, tf: c.Structure.Timeframe, key: fmt.Sprintf("%s|%d", c.StructuralID, c.Reaction.ConfirmationBarTime)}
				if c.DetectorConfluence != nil {
					su.stars = c.DetectorConfluence.SelectedStars
				}
				su.roomR, su.touches, su.position = c.Quality.Components["room_r"], c.Quality.Components["zone_touches"], math.NaN()
				if v, ok := c.Quality.Components["pd_position"]; ok {
					su.position = v
				}
				for _, ev := range c.Evidence {
					if ev.Code == "m5_supply_zone_premium" || ev.Code == "m5_demand_zone_discount" {
						su.awarded = true
					}
				}
				if frameContext := e.Context.Timeframes[c.Structure.Timeframe]; frameContext != nil && frameContext.Legacy != nil {
					su.trend = frameContext.Legacy.Structure
					if rng := frameContext.Legacy.Range; rng != nil {
						eq := float64(rng.Equilibrium)
						if side == "SELL" {
							su.oldAwarded = c.Structure.Low >= eq
						} else {
							su.oldAwarded = c.Structure.High <= eq
						}
					}
				}
				r.sets[name][su.key] = su
			}
			if reporter, ok := s.(strategy.RefusalReporter); ok {
				for k, n := range reporter.TakeRefusals() {
					r.refusal[name][string(k.Timeframe)+"/"+k.Reason] += n
				}
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func TestTheMeasurementRunsOnTheGBPJPYWindow(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	r := replay(t, doc, filepath.Join("..", "..", "testdata", "fvgwindow-gbpjpy-20261029.json"))
	for _, name := range []string{"supply_v9", "demand_v7"} {
		for key, s := range r.sets[name] {
			if s.stars < 2 {
				t.Errorf("%s published %s with %d stars: the confluence floor is 2", name, key, s.stars)
			}
		}
	}
	// the points are score inputs: switching them off can only remove setups, never add one
	for _, pair := range [][2]string{{"supply_v9_pd0", "supply_v9"}, {"demand_v7_pd0", "demand_v7"}} {
		for key := range r.sets[pair[0]] {
			if _, ok := r.sets[pair[1]][key]; !ok && len(r.sets[pair[1]]) > 0 {
				t.Errorf("%s publishes %s that %s does not", pair[0], key, pair[1])
			}
		}
	}
}

func TestMeasurePremiumDiscount(t *testing.T) {
	out := os.Getenv("PD_REPORT")
	if out == "" {
		t.Skip("set PD_REPORT=<file> to measure")
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	paths := captures()
	runs := make([]run, len(paths))
	var wg sync.WaitGroup
	gate := make(chan struct{}, runtime.NumCPU())
	for i := range paths {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			gate <- struct{}{}
			defer func() { <-gate }()
			runs[i] = replay(t, doc, paths[i])
		}(i)
	}
	wg.Wait()

	var sb strings.Builder
	p := func(format string, args ...any) { fmt.Fprintf(&sb, format+"\n", args...) }
	order := []struct{ side, before, mid, after string }{{"SELL", "supply_v8", "supply_v9_pd0", "supply_v9"}, {"BUY", "demand_v6", "demand_v7_pd0", "demand_v7"}}

	p("## 1. Published setups (a structure + a confirmation bar) per symbol and structure timeframe\n")
	p("Supply: v8 (master) -> v9 with pd_position_score 0 (the owned zone score with its own higher-timeframe zones) -> v9. Demand: v6 (master, shared spine) -> v7 with pd_position_score 0 (own package: M15/H1, no cross-side competition, room, own score) -> v7.\n")
	p("| side | symbol | tf | before | points off | after |\n|---|---|---|---:|---:|---:|")
	type cell struct{ b, m, a int }
	for _, o := range order {
		cells := map[string]*cell{}
		bump := func(name, symbol string, tf market.Timeframe, f func(c *cell)) {
			key := fmt.Sprintf("%s|%s", symbol, tf)
			c := cells[key]
			if c == nil {
				c = &cell{}
				cells[key] = c
			}
			f(c)
		}
		for _, r := range runs {
			for _, s := range r.sets[o.before] {
				bump(o.before, s.symbol, s.tf, func(c *cell) { c.b++ })
			}
			for _, s := range r.sets[o.mid] {
				bump(o.mid, s.symbol, s.tf, func(c *cell) { c.m++ })
			}
			for _, s := range r.sets[o.after] {
				bump(o.after, s.symbol, s.tf, func(c *cell) { c.a++ })
			}
		}
		keys := make([]string, 0, len(cells))
		for k := range cells {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		tb, tm, ta := 0, 0, 0
		for _, k := range keys {
			parts := strings.Split(k, "|")
			c := cells[k]
			tb, tm, ta = tb+c.b, tm+c.m, ta+c.a
			p("| %s | %s | %s | %d | %d | %d |", o.side, parts[0], parts[1], c.b, c.m, c.a)
		}
		p("| **%s** | all | all | **%d** | **%d** | **%d** |", o.side, tb, tm, ta)
	}

	p("\n## 2. Premium / discount points by side and trend state of the zone's frame\n")
	p("Over the published setups of v9 / v7. `old reading` is what the shared reading (the frame's fib dealing range, chosen around current price) would have awarded the same zone at the same bar; `new reading` is what the owned range awarded. trend = the frame's market structure at the decision bar.\n")
	p("| side | trend | published | old reading awards | new reading awards | median pd_position |\n|---|---|---:|---:|---:|---:|")
	for _, o := range order {
		type agg struct {
			n, oldN, newN int
			pos           []float64
		}
		by := map[string]*agg{}
		for _, r := range runs {
			for _, s := range r.sets[o.after] {
				a := by[s.trend]
				if a == nil {
					a = &agg{}
					by[s.trend] = a
				}
				a.n++
				if s.oldAwarded {
					a.oldN++
				}
				if s.awarded {
					a.newN++
				}
				if !math.IsNaN(s.position) {
					a.pos = append(a.pos, s.position)
				}
			}
		}
		trends := make([]string, 0, len(by))
		for k := range by {
			trends = append(trends, k)
		}
		sort.Strings(trends)
		for _, k := range trends {
			a := by[k]
			sort.Float64s(a.pos)
			med := math.NaN()
			if len(a.pos) > 0 {
				med = a.pos[len(a.pos)/2]
			}
			p("| %s | %s | %d | %d | %d | %.2f |", o.side, k, a.n, a.oldN, a.newN, med)
		}
	}

	p("\n## 3. Stars, and the setups that exist only because of the points\n")
	p("| side | stars | before | after |\n|---|---:|---:|---:|")
	for _, o := range order {
		hb, ha := map[int]int{}, map[int]int{}
		for _, r := range runs {
			for _, s := range r.sets[o.before] {
				hb[s.stars]++
			}
			for _, s := range r.sets[o.after] {
				ha[s.stars]++
			}
		}
		for _, stars := range []int{1, 2, 3} {
			p("| %s | %d | %d | %d |", o.side, stars, hb[stars], ha[stars])
		}
		crossing := 0
		for _, r := range runs {
			for key := range r.sets[o.after] {
				if _, ok := r.sets[o.mid][key]; !ok {
					crossing++
				}
			}
		}
		p("| %s | reach the 2-star floor only through the points | | %d |", o.side, crossing)
	}

	p("\n## 4. XAU SELL / BUY of the published supply + demand setups\n")
	var sellB, buyB, sellA, buyA int
	for _, r := range runs {
		if r.symbol != "XAU" {
			continue
		}
		sellB += len(r.sets["supply_v8"])
		buyB += len(r.sets["demand_v6"])
		sellA += len(r.sets["supply_v9"])
		buyA += len(r.sets["demand_v7"])
	}
	ratio := func(s, b int) string {
		if b == 0 {
			return "n/a"
		}
		return fmt.Sprintf("%.2f", float64(s)/float64(b))
	}
	p("| | SELL | BUY | SELL/BUY |\n|---|---:|---:|---:|\n| before (supply v8 + demand v6) | %d | %d | %s |\n| after (supply v9 + demand v7) | %d | %d | %s |", sellB, buyB, ratio(sellB, buyB), sellA, buyA, ratio(sellA, buyA))

	p("\n## 5. Refusals (per evaluated M5 bar; a structure that stays refused is counted again on every bar) and the published set by room_r and touches\n")
	for _, o := range order {
		total := map[string]int64{}
		for _, r := range runs {
			for k, n := range r.refusal[o.after] {
				total[k] += n
			}
		}
		keys := make([]string, 0, len(total))
		for k := range total {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		p("\n%s refusals: ", o.side)
		for _, k := range keys {
			p("- %s: %d", k, total[k])
		}
		room, touch := [4]int{}, [3]int{}
		for _, r := range runs {
			for _, s := range r.sets[o.after] {
				switch {
				case s.roomR < 1:
					room[0]++
				case s.roomR < 1.5:
					room[1]++
				case s.roomR < 2:
					room[2]++
				default:
					room[3]++
				}
				touch[minInt(int(s.touches), 2)]++
			}
		}
		p("\n| %s published by room_r | <1 | 1-1.5 | 1.5-2 | >=2 |\n|---|---:|---:|---:|---:|\n| setups | %d | %d | %d | %d |", o.side, room[0], room[1], room[2], room[3])
		p("\n| %s published by touches | 0 | 1 | 2 |\n|---|---:|---:|---:|\n| setups | %d | %d | %d |", o.side, touch[0], touch[1], touch[2])
	}
	// 6. cross-side: bars where master's demand was silent because a stronger SELL decision won the bar, and v7 publishes
	p("\n## 6. Bars where master's demand publisher was suppressed by a SELL decision of the same bar\n")
	suppressed := 0
	var examples []string
	for i, r := range runs {
		sellBars := map[int64]bool{}
		for _, s := range r.sets["supply_v6"] {
			sellBars[s.bar] = true
		}
		v6Bars := map[int64]bool{}
		for _, s := range r.sets["demand_v6"] {
			v6Bars[s.bar] = true
		}
		for key, s := range r.sets["demand_v7"] {
			if _, was := r.sets["demand_v6"][key]; !was && sellBars[s.bar] && !v6Bars[s.bar] {
				suppressed++
				if len(examples) < 5 {
					examples = append(examples, fmt.Sprintf("%s bar %d %s %s", filepath.Base(paths[i]), s.bar, s.tf, key))
				}
			}
		}
	}
	newOnly := 0
	for _, r := range runs {
		for key := range r.sets["demand_v7"] {
			if _, was := r.sets["demand_v6"][key]; !was {
				newOnly++
			}
		}
	}
	p("%d demand v7 setups are not v6 setups (any cause: the cross-side competition, the owned zone score and its own higher-timeframe points, the corrected range, a next-best zone publishing after a room refusal).\n", newOnly)
	p("%d demand v7 setups were published on a bar where master (the shared publisher, vendored) published a SELL and no BUY. First examples:\n", suppressed)
	for _, e := range examples {
		p("- %s", e)
	}
	if err := os.WriteFile(out, []byte(sb.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Log("\n" + sb.String())
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// On master the demand publisher took the best decision of the bar across supply AND demand zones and published it only if it was a BUY. On this bar (13 Oct
// 2026, GBPJPY) master's supply published a SELL, so master's demand published nothing; demand v7 competes among BUY zones only and publishes its own setup,
// the M15 zone formed at 1791257400.
func TestADemandSetupMasterSuppressedWithAStrongerSupplyReactionIsPublished(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	const bar = 1791269400
	r := replay(t, doc, filepath.Join("..", "..", "testdata", "replay-gbpjpy-production-capture-20261013.json"))
	onBar := func(name string) []setup {
		var out []setup
		for _, s := range r.sets[name] {
			if s.bar == bar {
				out = append(out, s)
			}
		}
		return out
	}
	if got := onBar("supply_v6"); len(got) == 0 {
		t.Fatal("master's supply did not publish a SELL on the bar: the fixture is wrong")
	}
	if got := onBar("demand_v6"); len(got) != 0 {
		t.Fatalf("master's demand published on the bar: %v", got)
	}
	got := onBar("demand_v7")
	if len(got) != 1 || !strings.HasPrefix(got[0].key, "technique:supply_demand:buy:1791257400@M15|") {
		t.Fatalf("demand v7 on the bar: %v, want the BUY of the M15 zone formed at 1791257400", got)
	}
	t.Logf("master: SELL %v, no BUY; demand v7: BUY %s, %d stars, room %.2f R", onBar("supply_v6")[0].key, got[0].key, got[0].stars, got[0].roomR)
}
