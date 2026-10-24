// Package htfzones_test measures where the zone strategies (supply, demand, order_block, fvg) find their setups: on M5 structure, or on M15/H1 structure
// confirmed and executed on M5.
//
//	HTF_ZONES_OUT=/tmp/h go test ./test/htfzones -run TestMeasureHTFZones -timeout 40m
//
// Every committed M5 capture is replayed through the engine exactly as a live feed is (the stop budget is switched off so the stop distribution is
// whole; the budget itself is proven in test/stopbudget). For each confirmed candidate of the four strategies it records the structure timeframe, the stop
// distance from the proximal entry edge and its class against the instrument envelope. Run it on the base tree and on the changed tree to get before and
// after. Two switches narrow the replay on the changed tree:
//
//	HTF_ZONES_M15_ONLY=1   higher_timeframes = [M15] for the four strategies (isolates the proximity fix from the H1 export)
//	HTF_ZONES_M15_WINDOW=n replays the M15 detector frame with an n-bar window instead of 250 (the M15 ageing study)
//
// Nothing here is an outcome and nothing is tuned.
package htfzones_test

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

var zoneStrategies = map[string]bool{"supply": true, "demand": true, "order_block": true, "fvg": true}

type record struct {
	ID, Strategy, Symbol, Direction, Capture string
	Structure                                string // M5 | M15 | H1
	ConfirmTime, BarTime                     int64
	EntryLow, EntryHigh, Reference           float64
	Invalidation, Pip                        float64
	RiskPips, MinPips, MaxPips               float64
	Class                                    string // refused | structural | floor
	Pattern                                  string
	Evidence                                 []string
	// OverlapsM5 is set on a higher-timeframe record: a same-strategy, same-direction M5-structure candidate with an overlapping entry band was
	// confirmed within an hour of it.
	OverlapsM5 bool
	// NewlyOffered is set on a higher-timeframe record whose zone the higher frame's own last close would NOT have offered (the retest-proximity test
	// the engine used to apply on the higher frame's bars): the setups the proximity fix recovers.
	NewlyOffered bool
}

func classify(risk, min, max float64) string {
	switch {
	case max > 0 && risk > max+1e-9:
		return "refused"
	case min > 0 && risk < min:
		return "floor"
	default:
		return "structural"
	}
}

func evidenceCodes(c opportunity.Candidate) []string {
	var out []string
	for _, e := range c.Evidence {
		out = append(out, e.Code)
	}
	return out
}

type captureRun struct {
	records   []record
	days      map[string]bool // symbol|YYYY-MM-DD of the M5 bars replayed
	instances map[string]bool // higher-timeframe instances seen: tf|technique|side|low|high|origin time
}

func replayCapture(t *testing.T, doc *config.Document, name string, m15Only bool, m15Window int) captureRun {
	run := captureRun{days: map[string]bool{}, instances: map[string]bool{}}
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Error(err)
		return run
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Error(err)
		return run
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Error(err)
		return run
	}
	m5, err := capture.Candles(market.M5)
	if err != nil {
		t.Error(err)
		return run
	}
	for _, b := range m5 {
		run.days[string(capture.Symbol)+"|"+timeDay(b.Time)] = true
	}
	opts := replaycapture.Options{WithoutStopBudget: true}
	if m15Only {
		opts.StrategyParameters = map[string]map[string]any{}
		for id := range zoneStrategies {
			opts.StrategyParameters[id] = map[string]any{"higher_timeframes": []any{"M15"}}
		}
	}
	if m15Window > 0 {
		opts.LegacyWindowBars = map[market.Timeframe]int{market.M15: m15Window}
	}
	seen := map[string]bool{}
	opts.OnEvaluation = func(e engine.Evaluation) {
		// What the higher frame's last close would have offered (proximity on), per timeframe, for this evaluation.
		type band struct {
			side      string
			low, high float64
		}
		oldOffered := map[string]map[band]bool{}
		for _, tf := range []market.Timeframe{market.M15, market.H1} {
			frame := e.Context.Timeframes[tf]
			if frame == nil || frame.Legacy == nil {
				continue
			}
			set := map[band]bool{}
			if len(frame.Legacy.Bars) >= 10 {
				for _, in := range techniquezone.InstancesFromScoredZones(frame.Legacy.TechniqueZones, frame.Legacy.Bars, techniquezone.ATRScalar(frame.Legacy.ATR, 1), settings.TechniqueZones.Technique) {
					set[band{in.Side, in.Low, in.High}] = true
				}
			}
			oldOffered[string(tf)] = set
		}
		for _, tf := range []market.Timeframe{market.M15, market.H1} {
			frame := e.Context.Timeframes[tf]
			if frame == nil || frame.Legacy == nil || frame.Legacy.Techniques == nil {
				continue
			}
			for _, in := range frame.Legacy.Techniques() {
				if in.OriginIndex < 0 || in.OriginIndex >= len(frame.Legacy.Bars) {
					continue
				}
				key := fmt.Sprintf("%s|%s|%s|%.5f|%.5f|%d", tf, in.Technique, in.Side, in.Low, in.High, frame.Legacy.Bars[in.OriginIndex].Time)
				run.instances[key] = true
			}
		}
		for _, c := range e.Candidates {
			strategy := string(c.Strategy)
			if !zoneStrategies[strategy] || c.Reaction == nil || seen[c.ID] {
				continue
			}
			seen[c.ID] = true
			buy := c.Direction == market.Buy
			reference := c.Entry.High
			if !buy {
				reference = c.Entry.Low
			}
			pip := settings.Geometry.PipSize
			risk := math.Abs(float64(reference)-float64(c.Invalidation.Price)) / pip
			structure := string(c.StructureTimeframe)
			if structure == "" {
				structure = "M5"
			}
			side := "sell"
			if buy {
				side = "buy"
			}
			newly := false
			if structure != "M5" {
				newly = !oldOffered[structure][band{side, float64(c.Entry.Low), float64(c.Entry.High)}]
			}
			run.records = append(run.records, record{NewlyOffered: newly, ID: c.ID, Strategy: strategy, Symbol: string(c.Symbol), Direction: string(c.Direction), Capture: name, Structure: structure,
				ConfirmTime: c.Reaction.ConfirmationBarTime, BarTime: e.BarTime, EntryLow: float64(c.Entry.Low), EntryHigh: float64(c.Entry.High), Reference: float64(reference),
				Invalidation: float64(c.Invalidation.Price), Pip: pip, RiskPips: risk, MinPips: settings.InstrumentStopMinPips, MaxPips: settings.InstrumentStopMaxPips,
				Class: classify(risk, settings.InstrumentStopMinPips, settings.InstrumentStopMaxPips), Pattern: c.Reaction.Pattern, Evidence: evidenceCodes(c)})
		}
	}
	if _, err := replaycapture.Replay(doc, capture, market.M5, opts); err != nil {
		t.Error(err)
	}
	return run
}

func timeDay(unix int64) string { return strconv.FormatInt(unix/86400, 10) }

func TestMeasureHTFZones(t *testing.T) {
	dir := os.Getenv("HTF_ZONES_OUT")
	if dir == "" {
		t.Skip("HTF_ZONES_OUT not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	m15Only := os.Getenv("HTF_ZONES_M15_ONLY") == "1"
	m15Window, _ := strconv.Atoi(os.Getenv("HTF_ZONES_M15_WINDOW"))
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	matches, _ := filepath.Glob(filepath.Join("..", "..", "testdata", "replay-*-production-capture-*.json"))
	for _, m := range matches {
		if !strings.Contains(m, "-m1-") {
			names = append(names, filepath.Base(m))
		}
	}
	sort.Strings(names)
	var mu sync.Mutex
	var all []record
	days, instances := map[string]bool{}, map[string]bool{}
	var wg sync.WaitGroup
	for _, name := range names {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			run := replayCapture(t, doc, name, m15Only, m15Window)
			mu.Lock()
			all = append(all, run.records...)
			for d := range run.days {
				days[d] = true
			}
			for k := range run.instances {
				instances[k] = true
			}
			mu.Unlock()
		}(name)
	}
	wg.Wait()
	sort.SliceStable(all, func(i, j int) bool {
		if all[i].Capture != all[j].Capture {
			return all[i].Capture < all[j].Capture
		}
		if all[i].BarTime != all[j].BarTime {
			return all[i].BarTime < all[j].BarTime
		}
		return all[i].ID < all[j].ID
	})
	// Overlapping captures replay the same period: the id alone identifies an opportunity.
	seen := map[string]bool{}
	var records []record
	for _, r := range all {
		if !seen[r.ID] {
			seen[r.ID] = true
			records = append(records, r)
		}
	}
	markOverlap(records)
	write := func(name string, v any) {
		raw, err := json.MarshalIndent(v, "", " ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, name), append(raw, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	out, err := os.Create(filepath.Join(dir, "records.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	enc := json.NewEncoder(out)
	for _, r := range records {
		if err := enc.Encode(r); err != nil {
			t.Fatal(err)
		}
	}
	out.Close()
	keys := make([]string, 0, len(instances))
	for k := range instances {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	write("instances.json", keys)
	write("results.json", aggregate(records, days))
	t.Logf("%d confirmed zone-strategy candidates across %d captures (m15_only=%v m15_window=%d)", len(records), len(names), m15Only, m15Window)
}

// markOverlap flags a higher-timeframe record that coincides, same strategy, symbol and direction with an overlapping entry band, with an M5-structure
// record confirmed within an hour: the thesis overlap the arbitration and exposure layers will see.
func markOverlap(records []record) {
	type key struct{ strategy, symbol, direction string }
	m5 := map[key][]int{}
	for i, r := range records {
		if r.Structure == "M5" {
			k := key{r.Strategy, r.Symbol, r.Direction}
			m5[k] = append(m5[k], i)
		}
	}
	for i := range records {
		r := &records[i]
		if r.Structure == "M5" {
			continue
		}
		for _, j := range m5[key{r.Strategy, r.Symbol, r.Direction}] {
			o := records[j]
			if math.Abs(float64(o.ConfirmTime-r.ConfirmTime)) <= 3600 && o.EntryLow <= r.EntryHigh && r.EntryLow <= o.EntryHigh {
				r.OverlapsM5 = true
				break
			}
		}
	}
}

func quantile(v []float64, q float64) float64 {
	if len(v) == 0 {
		return 0
	}
	s := append([]float64(nil), v...)
	sort.Float64s(s)
	return s[int(math.Round(q*float64(len(s)-1)))]
}

func round(v float64) float64 { return math.Round(v*100) / 100 }

type cell struct {
	N            int            `json:"n"`
	PerSymbolDay float64        `json:"per_symbol_day"`
	MedianPips   float64        `json:"stop_pips_median"`
	P75Pips      float64        `json:"stop_pips_p75"`
	Refused      int            `json:"refused_over_cap"`
	Structural   int            `json:"structural_in_budget"`
	Floor        int            `json:"floor_below_min"`
	Patterns     map[string]int `json:"patterns"`
	Overlap      int            `json:"overlaps_m5_candidate_within_1h"`
	OverlapShare float64        `json:"overlap_share"`
	SymbolDays   int            `json:"symbol_days"`
}

func aggregate(records []record, days map[string]bool) map[string]any {
	symbolDays := map[string]int{}
	for d := range days {
		symbolDays[strings.SplitN(d, "|", 2)[0]]++
	}
	classOf := func(s string) string {
		if s == "XAU" {
			return "XAU"
		}
		return "FX"
	}
	classDays := map[string]int{"XAU": symbolDays["XAU"]}
	for s, n := range symbolDays {
		if s != "XAU" {
			classDays["FX"] += n
		}
	}
	groups := map[string][]record{}
	for _, r := range records {
		syms := []string{r.Symbol, "all"}
		if classOf(r.Symbol) != r.Symbol { // XAU is its own class
			syms = append(syms, classOf(r.Symbol))
		}
		for _, sym := range syms {
			groups[r.Strategy+"|"+r.Structure+"|"+sym] = append(groups[r.Strategy+"|"+r.Structure+"|"+sym], r)
			groups["all|"+r.Structure+"|"+sym] = append(groups["all|"+r.Structure+"|"+sym], r)
		}
	}
	cells := map[string]cell{}
	for k, rs := range groups {
		sym := k[strings.LastIndex(k, "|")+1:]
		c := cell{Patterns: map[string]int{}}
		var pips []float64
		for _, r := range rs {
			c.N++
			pips = append(pips, r.RiskPips)
			c.Patterns[r.Pattern]++
			switch r.Class {
			case "refused":
				c.Refused++
			case "floor":
				c.Floor++
			default:
				c.Structural++
			}
			if r.OverlapsM5 {
				c.Overlap++
			}
		}
		c.MedianPips, c.P75Pips = round(quantile(pips, .5)), round(quantile(pips, .75))
		days := symbolDays[sym]
		if sym == "XAU" || sym == "FX" {
			days = classDays[sym]
		} else if sym == "all" {
			days = 0
			for _, n := range symbolDays {
				days += n
			}
		}
		c.SymbolDays = days
		if days > 0 {
			c.PerSymbolDay = round(float64(c.N) / float64(days))
		}
		if c.N > 0 {
			c.OverlapShare = round(float64(c.Overlap) / float64(c.N))
		}
		cells[k] = c
	}
	return map[string]any{
		"description":                  "confirmed candidates of supply, demand, order_block and fvg over the committed M5 captures by structure timeframe, stop budget off; stop distance is from the proximal entry edge to the published invalidation; per_symbol_day divides by the distinct UTC days each symbol's captures cover",
		"by_strategy_structure_symbol": cells,
	}
}
