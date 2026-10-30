// Package candlewindows_test measures what the candle-window fixes change (docs/analysis-engine-windows.md): structures older than the M5 execution window
// stay tradable (binding), the session levels of a frame come from its whole in-memory history (sessions), and, as an A/B only, what a wider M5 detection
// window would do. It publishes counts, not profit.
package candlewindows_test

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
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/fvg"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/orderblock"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/supply"
)

var windowed = []string{"fvg", "order_block", "supply"}

func resolve(t testing.TB) *config.Document {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

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

// trio is the three windowed strategies built from the engine's own settings; bounded drops a structure older than the M5 window, as the frozen analysis did.
type trio map[string]strategy.Strategy

func newTrio(t testing.TB, doc *config.Document, symbol market.Symbol, bounded bool) trio {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(symbol)); err != nil {
		t.Fatal(err)
	}
	if bounded {
		engine.WithFrozenBinding(&settings)
	}
	out := trio{}
	for _, cfg := range settings.Strategies {
		var s strategy.Strategy
		switch cfg.ID {
		case "fvg":
			s, err = fvg.New(cfg)
		case "order_block":
			s, err = orderblock.New(cfg)
		case "supply":
			s, err = supply.New(cfg)
		default:
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		out[string(cfg.ID)] = s
	}
	return out
}

type setup struct {
	strategy, structure string
	timeframe           market.Timeframe
	formedAt, confirmed int64
}

func (s setup) key() string { return fmt.Sprintf("%s|%s|%d", s.strategy, s.structure, s.confirmed) }

func setupOf(c opportunity.Candidate) setup {
	return setup{strategy: string(c.Strategy), structure: c.StructuralID, timeframe: c.Structure.Timeframe, formedAt: c.Structure.FormedAt, confirmed: c.Reaction.ConfirmationBarTime}
}

func refusalsOf(s strategy.Strategy) map[string]int64 {
	out := map[string]int64{}
	if reporter, ok := s.(strategy.RefusalReporter); ok {
		for r, n := range reporter.TakeRefusals() {
			out[string(r.Timeframe)+"/"+r.Reason] += n
		}
	}
	return out
}

// binding is what A1 changes on one capture: the three strategies on the SAME live contexts, bounded and not.
type binding struct {
	symbol     market.Symbol
	bounded    map[string]setup
	unbounded  map[string]setup
	refBounded map[string]map[string]int64 // strategy -> timeframe/reason -> count
	refOpen    map[string]map[string]int64
	bars       int
}

func measureBinding(t testing.TB, doc *config.Document, path string) binding {
	t.Helper()
	capture, err := replaycapture.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	b := binding{symbol: capture.Symbol, bounded: map[string]setup{}, unbounded: map[string]setup{}, refBounded: map[string]map[string]int64{}, refOpen: map[string]map[string]int64{}}
	bounded, open := newTrio(t, doc, capture.Symbol, true), newTrio(t, doc, capture.Symbol, false)
	for _, id := range windowed {
		b.refBounded[id], b.refOpen[id] = map[string]int64{}, map[string]int64{}
	}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		b.bars++
		for _, id := range windowed {
			for _, c := range bounded[id].Evaluate(e.Context) {
				s := setupOf(c)
				b.bounded[s.key()] = s
			}
			for k, n := range refusalsOf(bounded[id]) {
				b.refBounded[id][k] += n
			}
			for _, c := range open[id].Evaluate(e.Context) {
				s := setupOf(c)
				b.unbounded[s.key()] = s
			}
			for k, n := range refusalsOf(open[id]) {
				b.refOpen[id][k] += n
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return b
}

const m5WindowSeconds = 150 * 300

// The 29 Oct window of production (testdata/obwindow-xau-20261029.json): a structure older than 12.5 hours that touches and confirms is dropped by the
// frozen binding and evaluated by the fixed one. Whether it then publishes or is refused is reported, not tuned.
func TestAnOldStructureOfTheXAUWindowIsDroppedBeforeAndEvaluatedAfter(t *testing.T) {
	doc := resolve(t)
	b := measureBinding(t, doc, filepath.Join("..", "..", "testdata", "obwindow-xau-20261029.json"))
	var old []setup
	for k, s := range b.unbounded {
		if _, was := b.bounded[k]; was {
			continue
		}
		if age := s.confirmed - s.formedAt; age > m5WindowSeconds {
			old = append(old, s)
		}
	}
	sort.Slice(old, func(i, j int) bool { return old[i].confirmed < old[j].confirmed })
	for _, s := range old {
		t.Logf("newly published: %s %s formed %s confirmed %s age %.1f h", s.strategy, s.timeframe, time.Unix(s.formedAt, 0).UTC().Format("02 15:04"), time.Unix(s.confirmed, 0).UTC().Format("02 15:04"), float64(s.confirmed-s.formedAt)/3600)
	}
	for _, id := range windowed {
		t.Logf("%s refusals, bounded %v, not bounded %v", id, b.refBounded[id], b.refOpen[id])
	}
	if len(old) == 0 {
		t.Fatalf("no structure older than the M5 window was published on the XAU window: %d published bounded, %d not bounded; refusals %v vs %v", len(b.bounded), len(b.unbounded), b.refBounded, b.refOpen)
	}
	for k, s := range b.bounded {
		if _, ok := b.unbounded[k]; !ok {
			t.Errorf("fixing the binding removed %s", s.key())
		}
	}
}

// sessionRun is one replay's session levels and the decisions that read them.
type sessionRun struct {
	levels   map[int64]map[string]float64 // bar -> M5-frame level name -> price (finalized levels)
	decision map[string]decisionFact      // opportunity id -> facts
}

type decisionFact struct {
	strategy string
	stars    int
	session  bool
}

var sessionReaders = map[string]bool{"key_level": true, "flip_zone": true, "trendline": true, "ifvg": true}

func replaySessions(t testing.TB, doc *config.Document, path string, opts replaycapture.Options) sessionRun {
	t.Helper()
	capture, err := replaycapture.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	run := sessionRun{levels: map[int64]map[string]float64{}, decision: map[string]decisionFact{}}
	opts.OnEvaluation = func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		levels := map[string]float64{}
		if frame := e.Context.Timeframes[market.M5]; frame != nil && frame.Legacy != nil {
			for _, s := range frame.Legacy.Sessions {
				levels[fmt.Sprintf("%s@%d", s.Name, s.AvailableAt)] = s.Price
			}
		}
		run.levels[e.BarTime] = levels
		for _, c := range e.Candidates {
			if !sessionReaders[string(c.Strategy)] {
				continue
			}
			conf := c.DetectorConfluence
			if conf == nil && c.Technical != nil {
				conf = c.Technical.Confluence
			}
			if conf == nil {
				continue
			}
			run.decision[c.ID] = decisionFact{strategy: string(c.Strategy), stars: conf.SelectedStars, session: conf.Factors.SessionContext}
		}
	}
	if _, err := replaycapture.Replay(doc, capture, market.M5, opts); err != nil {
		t.Fatal(err)
	}
	return run
}

type windowRun struct {
	published map[string]map[string]bool // strategy -> opportunity ids
	refusals  map[string]map[string]int64
	latency   []time.Duration
}

func replayWindow(t testing.TB, doc *config.Document, path string, m5 int) windowRun {
	t.Helper()
	capture, err := replaycapture.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	run := windowRun{published: map[string]map[string]bool{}, refusals: map[string]map[string]int64{}}
	open := newTrio(t, doc, capture.Symbol, false)
	for _, id := range windowed {
		run.refusals[id] = map[string]int64{}
	}
	last := time.Now()
	opts := replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		now := time.Now()
		run.latency = append(run.latency, now.Sub(last))
		for _, id := range windowed {
			open[id].Evaluate(e.Context)
			for k, n := range refusalsOf(open[id]) {
				run.refusals[id][k] += n
			}
		}
		for _, c := range e.Candidates {
			if run.published[string(c.Strategy)] == nil {
				run.published[string(c.Strategy)] = map[string]bool{}
			}
			run.published[string(c.Strategy)][c.ID] = true
		}
		last = time.Now()
	}}
	if m5 > 0 {
		opts.LegacyWindowBars = map[market.Timeframe]int{market.M5: m5}
	}
	last = time.Now()
	if _, err := replaycapture.Replay(doc, capture, market.M5, opts); err != nil {
		t.Fatal(err)
	}
	return run
}

func median(d []time.Duration) time.Duration {
	if len(d) == 0 {
		return 0
	}
	c := append([]time.Duration(nil), d...)
	sort.Slice(c, func(i, j int) bool { return c[i] < c[j] })
	return c[len(c)/2]
}

// TestMeasureCandleWindows replays every committed capture and writes the tables of the pull request to CANDLE_WINDOWS_REPORT (skipped without it: it takes
// about a quarter of an hour).
func TestMeasureCandleWindows(t *testing.T) {
	out := os.Getenv("CANDLE_WINDOWS_REPORT")
	if out == "" {
		t.Skip("set CANDLE_WINDOWS_REPORT=<file> to measure")
	}
	doc := resolve(t)
	paths := captures()
	type result struct {
		path    string
		binding binding
		live    sessionRun
		frozen  sessionRun
		w288    windowRun
		w576    windowRun
		w150    windowRun
	}
	results := make([]result, len(paths))
	var wg sync.WaitGroup
	gate := make(chan struct{}, runtime.NumCPU())
	for i := range paths {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			gate <- struct{}{}
			defer func() { <-gate }()
			r := result{path: paths[i]}
			r.binding = measureBinding(t, doc, paths[i])
			r.live = replaySessions(t, doc, paths[i], replaycapture.Options{})
			r.frozen = replaySessions(t, doc, paths[i], replaycapture.Options{FrozenSessionWindows: true})
			r.w150 = replayWindow(t, doc, paths[i], 0)
			r.w288 = replayWindow(t, doc, paths[i], 288)
			r.w576 = replayWindow(t, doc, paths[i], 576)
			results[i] = r
		}(i)
	}
	wg.Wait()

	var sb strings.Builder
	p := func(format string, args ...any) { fmt.Fprintf(&sb, format+"\n", args...) }

	// A1 binding
	type cell struct{ bounded, open, newly int }
	cells := map[string]*cell{} // strategy|symbol|tf
	ages := map[string][]float64{}
	refB, refO := map[string]map[string]int64{}, map[string]map[string]int64{}
	for _, id := range windowed {
		refB[id], refO[id] = map[string]int64{}, map[string]int64{}
	}
	for _, r := range results {
		for k, s := range r.binding.bounded {
			_ = k
			c := cells[fmt.Sprintf("%s|%s|%s", s.strategy, r.binding.symbol, s.timeframe)]
			if c == nil {
				c = &cell{}
				cells[fmt.Sprintf("%s|%s|%s", s.strategy, r.binding.symbol, s.timeframe)] = c
			}
			c.bounded++
		}
		for k, s := range r.binding.unbounded {
			key := fmt.Sprintf("%s|%s|%s", s.strategy, r.binding.symbol, s.timeframe)
			c := cells[key]
			if c == nil {
				c = &cell{}
				cells[key] = c
			}
			c.open++
			if _, was := r.binding.bounded[k]; !was {
				c.newly++
				ages[s.strategy] = append(ages[s.strategy], float64(s.confirmed-s.formedAt)/3600)
			}
		}
		for _, id := range windowed {
			for k, n := range r.binding.refBounded[id] {
				refB[id][k] += n
			}
			for k, n := range r.binding.refOpen[id] {
				refO[id][k] += n
			}
		}
	}
	keys := make([]string, 0, len(cells))
	for k := range cells {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	p("## A1: binding (the same live contexts, the three strategies bounded to the M5 window and not)\n")
	p("| strategy | symbol | structure tf | published bounded | published now | newly published (origin older than the window at confirmation) |\n|---|---|---|---:|---:|---:|")
	for _, k := range keys {
		parts := strings.Split(k, "|")
		c := cells[k]
		p("| %s | %s | %s | %d | %d | %d |", parts[0], parts[1], parts[2], c.bounded, c.open, c.newly)
	}
	p("\nAge of the newly published setups (hours from the structure's origin bar to the confirmation):\n\n| strategy | newly published | min | median | p90 | max |\n|---|---:|---:|---:|---:|---:|")
	for _, id := range windowed {
		a := ages[id]
		sort.Float64s(a)
		if len(a) == 0 {
			p("| %s | 0 | | | | |", id)
			continue
		}
		p("| %s | %d | %.1f | %.1f | %.1f | %.1f |", id, len(a), a[0], a[len(a)/2], a[int(math.Floor(float64(len(a)-1)*0.9))], a[len(a)-1])
	}
	p("\nRefusals counted per evaluated M5 bar (a structure that stays refused is counted again on every bar), bounded -> now:\n\n| strategy | timeframe/reason | bounded | now |\n|---|---|---:|---:|")
	for _, id := range windowed {
		reasons := map[string]bool{}
		for k := range refB[id] {
			reasons[k] = true
		}
		for k := range refO[id] {
			reasons[k] = true
		}
		names := make([]string, 0, len(reasons))
		for k := range reasons {
			names = append(names, k)
		}
		sort.Strings(names)
		for _, k := range names {
			p("| %s | %s | %d | %d |", id, k, refB[id][k], refO[id][k])
		}
	}

	// A2 sessions
	changedBars, bars, changedLevels, levelsSeen := 0, 0, 0, 0
	type flip struct{ decisions, stars, session int }
	flips := map[string]*flip{}
	for _, r := range results {
		for bar, live := range r.live.levels {
			frozen := r.frozen.levels[bar]
			bars++
			changed := false
			for name, price := range live {
				levelsSeen++
				if old, ok := frozen[name]; !ok || math.Abs(old-price) > 1e-9 {
					changedLevels++
					changed = true
				}
			}
			for name := range frozen {
				if _, ok := live[name]; !ok {
					changed = true
				}
			}
			if changed {
				changedBars++
			}
		}
		for id, d := range r.live.decision {
			f := flips[d.strategy]
			if f == nil {
				f = &flip{}
				flips[d.strategy] = f
			}
			old, ok := r.frozen.decision[id]
			if !ok {
				continue
			}
			f.decisions++
			if old.stars != d.stars {
				f.stars++
			}
			if old.session != d.session {
				f.session++
			}
		}
	}
	p("\n## A2: session levels from the full in-memory history\n")
	p("M5-frame session levels (PDH/PDL, Asia/London/NY extremes, PWH/PWL as the frame carries them) over %d evaluated bars: %d level readings in the live run, %d of them differ from the window-derived value (or did not exist there); %d bars carry at least one different level.\n", bars, levelsSeen, changedLevels, changedBars)
	p("| strategy | decisions in both runs | stars changed | SessionContext changed |\n|---|---:|---:|---:|")
	names := make([]string, 0, len(flips))
	for k := range flips {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, k := range names {
		p("| %s | %d | %d | %d |", k, flips[k].decisions, flips[k].stars, flips[k].session)
	}

	// M5 window A/B
	p("\n## M5 detection window, A/B only (live windows otherwise; nothing is changed in the configuration)\n")
	p("| M5 window | fvg | order_block | supply | demand | key_level | flip_zone | trendline | ifvg | median evaluation (ms/bar, includes the replay) |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
	for _, w := range []struct {
		name string
		get  func(result) windowRun
	}{{"150 (production)", func(r result) windowRun { return r.w150 }}, {"288 (one day)", func(r result) windowRun { return r.w288 }}, {"576 (two days)", func(r result) windowRun { return r.w576 }}} {
		count := map[string]int{}
		var lat []time.Duration
		for _, r := range results {
			run := w.get(r)
			for id, set := range run.published {
				count[id] += len(set)
			}
			lat = append(lat, run.latency...)
		}
		p("| %s | %d | %d | %d | %d | %d | %d | %d | %d | %.1f |", w.name, count["fvg"], count["order_block"], count["supply"], count["demand"], count["key_level"], count["flip_zone"], count["trendline"], count["ifvg"], float64(median(lat))/1e6)
	}
	p("\nRefusals of the three windowed strategies by M5 window (per evaluated bar):\n\n| M5 window | strategy | timeframe/reason | count |\n|---|---|---|---:|")
	for _, w := range []struct {
		name string
		get  func(result) windowRun
	}{{"150", func(r result) windowRun { return r.w150 }}, {"288", func(r result) windowRun { return r.w288 }}, {"576", func(r result) windowRun { return r.w576 }}} {
		for _, id := range windowed {
			total := map[string]int64{}
			for _, r := range results {
				for k, n := range w.get(r).refusals[id] {
					total[k] += n
				}
			}
			names := make([]string, 0, len(total))
			for k := range total {
				names = append(names, k)
			}
			sort.Strings(names)
			for _, k := range names {
				p("| %s | %s | %s | %d |", w.name, id, k, total[k])
			}
		}
	}
	if err := os.WriteFile(out, []byte(sb.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Log("\n" + sb.String())
}
