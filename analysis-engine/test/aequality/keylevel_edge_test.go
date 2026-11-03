package aequality_test

import (
	"encoding/json"
	"fmt"
	"hash/fnv"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// TestKeyLevelEdgeSource answers, with the strategy's own logic and costs, whether the edge of key_level is in the LEVEL or in the REACTION (docs/strategies/keylevel-edge-source.md).
// The unchanged v6 strategy is replayed over every committed XAU M5 capture four ways: A (the live SwingLevels), B (every zigzag swing as its own level), C (random levels, 20
// seeds, as many as A has) and D (the v7 level definition); the opportunities are simulated like test/aequality's, one per level per 2 h.
//
//	KEYLEVEL_EDGE_OUT=/tmp/kle go test ./test/aequality -run TestKeyLevelEdgeSource -timeout 120m -v
//
// Optional: KEYLEVEL_EDGE_CAPTURES=substring,substring limits the captures; KEYLEVEL_EDGE_SEEDS=n sets the random seeds (default 20).
func TestKeyLevelEdgeSource(t *testing.T) {
	out := os.Getenv("KEYLEVEL_EDGE_OUT")
	if out == "" {
		t.Skip("KEYLEVEL_EDGE_OUT not set")
	}
	if err := os.MkdirAll(out, 0o755); err != nil {
		t.Fatal(err)
	}
	seeds := 20
	if raw := os.Getenv("KEYLEVEL_EDGE_SEEDS"); raw != "" {
		fmt.Sscanf(raw, "%d", &seeds)
	}
	doc, err := config.ResolveDocument(configPath())
	if err != nil {
		t.Fatal(err)
	}
	var list []capture
	for _, c := range edgeCaptures {
		if filter := os.Getenv("KEYLEVEL_EDGE_CAPTURES"); filter != "" {
			keep := false
			for _, f := range strings.Split(filter, ",") {
				keep = keep || strings.Contains(c.file, f)
			}
			if !keep {
				continue
			}
		}
		list = append(list, c)
	}

	type job struct {
		variant string
		seed    int
		c       int
	}
	var jobs []job
	for ci := range list {
		jobs = append(jobs, job{"A", 0, ci}, job{"B", 0, ci}, job{"D", 0, ci})
		for s := 1; s <= seeds; s++ {
			jobs = append(jobs, job{"C", s, ci})
		}
	}
	type replayed struct {
		records []*Record
		bars    []market.Candle
		index   map[int64]int
	}
	results := make([]replayed, len(jobs))
	sem := make(chan struct{}, 6)
	var wg sync.WaitGroup
	started := time.Now()
	for ji := range jobs {
		wg.Add(1)
		go func(ji int) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			j := jobs[ji]
			c := list[j.c]
			loaded, err := replaycapture.Load(filepath.Join("..", "..", "testdata", c.file))
			if err != nil {
				t.Error(err)
				return
			}
			bars, err := loaded.Candles(market.M5)
			if err != nil {
				t.Error(err)
				return
			}
			index := make(map[int64]int, len(bars))
			for i, b := range bars {
				index[b.Time] = i
			}
			var records []*Record
			opts := replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				for _, cand := range e.Candidates {
					if string(cand.Strategy) != "key_level" {
						continue
					}
					records = append(records, record(c, e, cand))
				}
			}}
			switch j.variant {
			case "B", "C":
				opts.StrategyParameters = map[string]map[string]any{"key_level": {"minimum_touches": 1}}
				if j.variant == "B" {
					opts.KeyLevelSource = swingAreaLevels
				} else {
					opts.KeyLevelSource = randomLevels(c.file, j.seed)
				}
			case "D":
				opts.StrategyParameters = map[string]map[string]any{"key_level": {"minimum_touches": 2}}
				opts.KeyLevelSource = v7Levels
			}
			if _, err := replaycapture.Replay(doc, loaded, market.M5, opts); err != nil {
				t.Error(err)
				return
			}
			results[ji] = replayed{records: records, bars: bars, index: index}
			t.Logf("%s %s seed %d: %d candidates (%s elapsed)", c.file, j.variant, j.seed, len(records), time.Since(started).Round(time.Second))
		}(ji)
	}
	wg.Wait()

	// first sight across overlapping captures (chronological), then one candidate per level per 2 h, then the candle-causal simulation with costs
	type outcomeSet struct {
		rec *Record
		bar recSeries
	}
	byVariant := map[string][]*outcomeSet{} // "A", "B", "D", "C1".."Cn"
	rawCount := map[string]int{}
	seen := map[string]map[string]bool{}
	for ji, j := range jobs {
		name := j.variant
		if j.variant == "C" {
			name = fmt.Sprintf("C%02d", j.seed)
		}
		if seen[name] == nil {
			seen[name] = map[string]bool{}
		}
		for _, rec := range results[ji].records {
			rawCount[name]++
			if seen[name][rec.ID] {
				continue
			}
			seen[name][rec.ID] = true
			byVariant[name] = append(byVariant[name], &outcomeSet{rec: rec, bar: recSeries{bars: results[ji].bars, index: results[ji].index, seconds: 300}})
		}
	}
	dedupedCount := map[string]int{}
	for name, sets := range byVariant {
		sort.SliceStable(sets, func(i, j int) bool {
			if sets[i].rec.EvalTime != sets[j].rec.EvalTime {
				return sets[i].rec.EvalTime < sets[j].rec.EvalTime
			}
			return sets[i].rec.ID < sets[j].rec.ID
		})
		lastKept := map[string]int64{}
		kept := sets[:0]
		for _, s := range sets {
			if last, ok := lastKept[s.rec.StructuralID]; ok && s.rec.EvalTime-last < 2*3600 {
				continue
			}
			lastKept[s.rec.StructuralID] = s.rec.EvalTime
			kept = append(kept, s)
		}
		byVariant[name] = kept
		dedupedCount[name] = len(kept)
	}
	units := map[string][]edgeUnit{}
	var umu sync.Mutex
	var uwg sync.WaitGroup
	usem := make(chan struct{}, 12)
	for name, sets := range byVariant {
		for chunk := 0; chunk < len(sets); chunk += 400 {
			end := chunk + 400
			if end > len(sets) {
				end = len(sets)
			}
			uwg.Add(1)
			go func(name string, part []*outcomeSet) {
				defer uwg.Done()
				usem <- struct{}{}
				defer func() { <-usem }()
				local := make([]edgeUnit, 0, len(part))
				for _, s := range part {
					r := s.rec
					u := edgeUnit{day: r.EvalTime / 86400, period: edgePeriod(r.EvalTime)}
					cost := spread[r.Symbol]
					sims := [3]outcome{
						simulate(r, s.bar.bars, s.bar.index, 300, "oneR", cost),
						simulate(r, s.bar.bars, s.bar.index, 300, "twoR", cost),
						simulate(r, s.bar.bars, s.bar.index, 300, "ladder", cost),
					}
					filled := func(o outcome) bool {
						return o.Result == "target" || o.Result == "stop" || o.Result == "stop_be" || o.Result == "timeout"
					}
					u.oneR, u.oneOK = sims[0].R, filled(sims[0])
					u.twoR, u.twoOK = sims[1].R, filled(sims[1])
					u.ladder, u.ladderOK = sims[2].R, filled(sims[2])
					if u.oneOK && sims[0].Result == "target" {
						u.win1R = 1
					}
					for li, ladder := range []string{"oneR", "twoR", "ladder"} {
						if filled(sims[li]) {
							u.rnd[li], u.rndOK[li] = matchedRandomMean(r, s.bar, ladder, 20)
						}
					}
					local = append(local, u)
				}
				umu.Lock()
				units[name] = append(units[name], local...)
				umu.Unlock()
			}(name, sets[chunk:end])
		}
	}
	uwg.Wait()

	report := buildEdgeReport(list, units, rawCount, dedupedCount, seeds)
	raw, err := json.MarshalIndent(report, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(out, "keylevel-edge-source.json"), append(raw, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Logf("wrote %s (%s)", filepath.Join(out, "keylevel-edge-source.json"), time.Since(started).Round(time.Second))
}

// edgeCaptures are every committed XAU capture with M5 bars, oldest first (the first capture to see an opportunity owns it).
var edgeCaptures = []capture{
	{"XAU Sep", "XAU", "replay-xau-production-capture-20260921.json"},
	{"XAU M1 Oct 2-6", "XAU", "replay-xau-m1-production-capture-20261006.json"},
	{"XAU Oct 12", "XAU", "replay-xau-production-capture-20261012.json"},
	{"XAU Oct 13", "XAU", "replay-xau-production-capture-20261013.json"},
	{"XAU Oct 14", "XAU", "replay-xau-production-capture-20261014.json"},
	{"XAU Oct 22", "XAU", "replay-xau-production-capture-20261022.json"},
	{"XAU ob window Oct 29", "XAU", "obwindow-xau-20261029.json"},
	{"XAU scalp window Nov 3", "XAU", "scalpwindow-xau-20261103.json"},
}

func edgePeriod(evalTime int64) string {
	at := time.Unix(evalTime, 0).UTC()
	switch {
	case at.Before(time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC)):
		return "before_1_Oct"
	case at.Before(time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC)):
		return "1_to_11_Oct"
	case at.Before(time.Date(2026, 10, 23, 0, 0, 0, 0, time.UTC)):
		return "12_to_22_Oct"
	default:
		return "23_Oct_to_3_Nov"
	}
}

// ---- level sources -------------------------------------------------------------------------------------------------------------------------------------

func frameMedianATR(frame *analysiscontext.LegacyFrame) float64 {
	return techniquezone.ATRScalar(frame.ATR, 1)
}

func medianTouches(levels []techniquezone.Level) int {
	if len(levels) == 0 {
		return 2
	}
	touches := make([]int, len(levels))
	for i, l := range levels {
		touches[i] = l.Touches
	}
	sort.Ints(touches)
	return touches[len(touches)/2]
}

// swingAreaLevels is variant B: every zigzag swing of the window is its own level (price = the swing, band = 0.5 x the median window ATR, touches fixed at A's median).
func swingAreaLevels(frame *analysiscontext.LegacyFrame, _ int64) []techniquezone.Level {
	tol := 0.5 * frameMedianATR(frame)
	touches := medianTouches(frame.SwingLevels)
	levels := make([]techniquezone.Level, 0, len(frame.Swings))
	for _, s := range frame.Swings {
		levels = append(levels, techniquezone.Level{Price: s.Price, Kind: "reaction", Touches: touches, Band: tol, Strength: float64(touches)})
	}
	return levels
}

// randomLevels is variant C: as many levels as A has, prices uniform in the window's [low, high], A's band and median touches; the draw depends on (capture, the evaluated bar, seed).
func randomLevels(captureName string, seed int) func(*analysiscontext.LegacyFrame, int64) []techniquezone.Level {
	return func(frame *analysiscontext.LegacyFrame, barTime int64) []techniquezone.Level {
		n := len(frame.SwingLevels)
		if n == 0 || len(frame.Bars) == 0 {
			return nil
		}
		low, high := frame.Bars[0].Low, frame.Bars[0].High
		for _, b := range frame.Bars {
			low, high = math.Min(low, b.Low), math.Max(high, b.High)
		}
		h := fnv.New64a()
		fmt.Fprintf(h, "%s|%d|%d", captureName, barTime, seed)
		rng := rand.New(rand.NewSource(int64(h.Sum64())))
		touches := medianTouches(frame.SwingLevels)
		band := 0.5 * frameMedianATR(frame)
		levels := make([]techniquezone.Level, 0, n)
		for i := 0; i < n; i++ {
			levels = append(levels, techniquezone.Level{Price: low + rng.Float64()*(high-low), Kind: "reaction", Touches: touches, Band: band, Strength: float64(touches)})
		}
		return levels
	}
}

// v7Levels is variant D, the PR-K2 definition computed from the frame's own swings and window ATR: clusters of >= 2 swings within 0.25 x the median ATR of every member (span <= 2 tol), round levels
// every 10 with >= 2 swings within max(tol, 0.25 ATR at the swing), adjacent levels within tol merged to the STRONGER member's own price (more touches, then reaction before round, then the more recent
// newest member; never an average; touches = max), and the origin kind (majority swing lows "support", highs "resistance", a tie "reaction") that keylevel.Role and the strategy's direction logic read.
func v7Levels(frame *analysiscontext.LegacyFrame, _ int64) []techniquezone.Level {
	const roundStep = 10.0
	swings := frame.Swings
	if len(swings) == 0 {
		return nil
	}
	tol := 0.25 * frameMedianATR(frame)
	type cand struct {
		price   float64
		touches int
		round   bool
		newest  int
	}
	var cands []cand
	order := make([]int, len(swings))
	for i := range order {
		order[i] = i
	}
	sort.SliceStable(order, func(a, b int) bool { return swings[order[a]].Price < swings[order[b]].Price })
	var cluster []int
	flush := func() {
		if len(cluster) >= 2 {
			sum, newest := 0.0, 0
			for _, i := range cluster {
				sum += swings[i].Price
				if swings[i].Index > newest {
					newest = swings[i].Index
				}
			}
			cands = append(cands, cand{price: sum / float64(len(cluster)), touches: len(cluster), newest: newest})
		}
	}
	for _, i := range order {
		if len(cluster) > 0 {
			// sorted ascending: within tol of every member is within tol of the lowest one, which also keeps the span <= 2 tol
			if swings[i].Price-swings[cluster[0]].Price > tol {
				flush()
				cluster = cluster[:0]
			}
		}
		cluster = append(cluster, i)
	}
	flush()
	lo, hi := swings[order[0]].Price, swings[order[len(order)-1]].Price
	atrAt := func(idx int) float64 {
		if idx < 0 || idx >= len(frame.ATR) || !(frame.ATR[idx] > 0) {
			return 1
		}
		return frame.ATR[idx]
	}
	lowStep := math.Floor(lo/roundStep) * roundStep
	for k := 0; k < int((math.Ceil(hi/roundStep)*roundStep-lowStep)/roundStep)+1; k++ {
		price := lowStep + float64(k)*roundStep
		touches, newest := 0, 0
		for _, s := range swings {
			if math.Abs(s.Price-price) <= math.Max(tol, atrAt(s.Index)*0.25) {
				touches++
				if s.Index > newest {
					newest = s.Index
				}
			}
		}
		if touches >= 2 {
			cands = append(cands, cand{price: price, touches: touches, round: true, newest: newest})
		}
	}
	sort.SliceStable(cands, func(a, b int) bool { return cands[a].price < cands[b].price })
	stronger := func(a, b cand) bool { // is a stronger than b
		if a.touches != b.touches {
			return a.touches > b.touches
		}
		if a.round != b.round {
			return !a.round
		}
		return a.newest > b.newest
	}
	var merged []cand
	for _, c := range cands {
		if n := len(merged); n > 0 && math.Abs(merged[n-1].price-c.price) <= tol {
			prev := merged[n-1]
			touches := prev.touches
			if c.touches > touches {
				touches = c.touches
			}
			winner := prev
			if stronger(c, prev) {
				winner = c
			}
			winner.touches = touches
			if prev.newest > winner.newest {
				winner.newest = prev.newest
			}
			merged[n-1] = winner
		} else {
			merged = append(merged, c)
		}
	}
	levels := make([]techniquezone.Level, 0, len(merged))
	for _, c := range merged {
		highs, lows := 0, 0
		for _, s := range swings {
			if math.Abs(s.Price-c.price) <= tol {
				if s.Kind == "high" {
					highs++
				} else {
					lows++
				}
			}
		}
		kind := "reaction"
		switch {
		case lows > highs:
			kind = "support"
		case highs > lows:
			kind = "resistance"
		}
		levels = append(levels, techniquezone.Level{Price: c.price, Kind: kind, Touches: c.touches, Band: tol, Strength: float64(c.touches)})
	}
	return levels
}

// ---- matched random entries ----------------------------------------------------------------------------------------------------------------------------

// matchedRandomMean is the mean R of the opportunity's matched random counterparts (the same direction, technical risk, cost, ladder and 24-hour horizon, entered at the close of a random bar of
// its capture instead of at the signal), as in TestMatchedRandomBaseline.
func matchedRandomMean(r *Record, series recSeries, ladder string, draws int) (float64, bool) {
	if len(series.bars) < 600 || r.TechRiskPips <= 0 {
		return 0, false
	}
	pip := pipSize[r.Symbol]
	h := fnv.New64a()
	fmt.Fprintf(h, "%s|%s", r.ID, ladder)
	rng := rand.New(rand.NewSource(int64(h.Sum64())))
	horizon := int(24 * 3600 / series.seconds)
	lo, hi := 200, len(series.bars)-horizon-2
	if hi <= lo {
		return 0, false
	}
	sum, filled := 0.0, 0
	for d := 0; d < draws; d++ {
		i := lo + rng.Intn(hi-lo)
		px := series.bars[i].Close
		synth := *r
		synth.Sim = nil
		synth.EvalTime = series.bars[i].Time + series.seconds
		synth.EntryLow, synth.EntryHigh = px, px
		if r.Direction == "BUY" {
			synth.Invalidation = px - r.TechRiskPips*pip
		} else {
			synth.Invalidation = px + r.TechRiskPips*pip
		}
		synth.ExpiresAt = 0
		o := simulate(&synth, series.bars, series.index, series.seconds, ladder, spread[r.Symbol])
		switch o.Result {
		case "target", "stop", "stop_be", "timeout":
			filled++
			sum += o.R
		}
	}
	if filled == 0 {
		return 0, false
	}
	return sum / float64(filled), true
}

// ---- statistics ----------------------------------------------------------------------------------------------------------------------------------------

type dayCell struct{ sum, n float64 }

// dayMean aggregates a per-opportunity value by UTC day (the cluster of the bootstrap).
type dayMean map[int64]*dayCell

func (d dayMean) add(day int64, v float64) {
	c := d[day]
	if c == nil {
		c = &dayCell{}
		d[day] = c
	}
	c.sum += v
	c.n++
}

func (d dayMean) total() (float64, float64) {
	s, n := 0.0, 0.0
	for _, c := range d {
		s += c.sum
		n += c.n
	}
	return s, n
}

func (d dayMean) mean() float64 {
	if s, n := d.total(); n > 0 {
		return s / n
	}
	return math.NaN()
}

// fnum is a float64 that marshals NaN / Inf as null (an empty cell of the report).
type fnum float64

func (f fnum) MarshalJSON() ([]byte, error) {
	if math.IsNaN(float64(f)) || math.IsInf(float64(f), 0) {
		return []byte("null"), nil
	}
	return json.Marshal(math.Round(float64(f)*1e5) / 1e5)
}

type ci struct {
	Mean fnum `json:"mean"`
	Lo   fnum `json:"ci95_lo"`
	Hi   fnum `json:"ci95_hi"`
	N    int  `json:"n"`
}

func quantile(sorted []float64, q float64) float64 {
	if len(sorted) == 0 {
		return math.NaN()
	}
	pos := q * float64(len(sorted)-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	return sorted[lo] + (sorted[hi]-sorted[lo])*(pos-float64(lo))
}

const bootstrapDraws = 2000

// bootstrapDiff resamples UTC days with replacement and returns the point estimate and 95 % interval of f(resampled days), where f gets, for each day multiplicity vector, the mean of every series.
func bootstrapDiff(days []int64, f func(weight map[int64]float64) float64) ci {
	rng := rand.New(rand.NewSource(20261104))
	point := func() float64 {
		w := map[int64]float64{}
		for _, d := range days {
			w[d] = 1
		}
		return f(w)
	}()
	draws := make([]float64, 0, bootstrapDraws)
	for b := 0; b < bootstrapDraws; b++ {
		w := map[int64]float64{}
		for range days {
			w[days[rng.Intn(len(days))]]++
		}
		if v := f(w); !math.IsNaN(v) {
			draws = append(draws, v)
		}
	}
	sort.Float64s(draws)
	return ci{Mean: fnum(point), Lo: fnum(quantile(draws, 0.025)), Hi: fnum(quantile(draws, 0.975))}
}

func weighted(d dayMean, w map[int64]float64) float64 {
	s, n := 0.0, 0.0
	for day, c := range d {
		if m := w[day]; m > 0 {
			s += m * c.sum
			n += m * c.n
		}
	}
	if n == 0 {
		return math.NaN()
	}
	return s / n
}

func allDays(sets ...dayMean) []int64 {
	seen := map[int64]bool{}
	for _, s := range sets {
		for d := range s {
			seen[d] = true
		}
	}
	days := make([]int64, 0, len(seen))
	for d := range seen {
		days = append(days, d)
	}
	sort.Slice(days, func(i, j int) bool { return days[i] < days[j] })
	return days
}

// ---- the report ----------------------------------------------------------------------------------------------------------------------------------------

// edgeUnit is one simulated opportunity: its outcome at three ladders (all with costs) and the mean R of its matched random counterparts.
type edgeUnit struct {
	day                    int64
	period                 string
	oneR, twoR, ladder     float64
	oneOK, twoOK, ladderOK bool
	win1R                  float64
	rnd                    [3]float64
	rndOK                  [3]bool
}

type metricSeries struct {
	oneR, twoR, ladder, win1R, edgeOne, edgeTwo, edgeLadder dayMean
	n                                                       int
}

// collect aggregates units by UTC day; period "" takes every unit.
func collect(units []edgeUnit, period string) *metricSeries {
	m := &metricSeries{oneR: dayMean{}, twoR: dayMean{}, ladder: dayMean{}, win1R: dayMean{}, edgeOne: dayMean{}, edgeTwo: dayMean{}, edgeLadder: dayMean{}}
	for _, u := range units {
		if period != "" && u.period != period {
			continue
		}
		if u.ladderOK {
			m.n++
			m.ladder.add(u.day, u.ladder)
			if u.rndOK[2] {
				m.edgeLadder.add(u.day, u.ladder-u.rnd[2])
			}
		}
		if u.oneOK {
			m.oneR.add(u.day, u.oneR)
			m.win1R.add(u.day, u.win1R)
			if u.rndOK[0] {
				m.edgeOne.add(u.day, u.oneR-u.rnd[0])
			}
		}
		if u.twoOK {
			m.twoR.add(u.day, u.twoR)
			if u.rndOK[1] {
				m.edgeTwo.add(u.day, u.twoR-u.rnd[1])
			}
		}
	}
	return m
}

var edgePeriods = []string{"before_1_Oct", "1_to_11_Oct", "12_to_22_Oct", "23_Oct_to_3_Nov"}

func selectMetric(m *metricSeries, name string) dayMean {
	switch name {
	case "oneR":
		return m.oneR
	case "twoR":
		return m.twoR
	case "ladder":
		return m.ladder
	case "win1R":
		return m.win1R
	case "edge_oneR":
		return m.edgeOne
	case "edge_twoR":
		return m.edgeTwo
	}
	return m.edgeLadder
}

var edgeMetrics = []string{"oneR", "twoR", "ladder", "win1R", "edge_oneR", "edge_twoR", "edge_ladder"}

func single(m *metricSeries, name string) ci {
	d := selectMetric(m, name)
	out := bootstrapDiff(allDays(d), func(w map[int64]float64) float64 { return weighted(d, w) })
	_, n := d.total()
	out.N = int(n)
	return out
}

// seedMean is the mean over the random seeds of each seed's day-weighted mean.
func seedMean(seedSets []*metricSeries, name string, w map[int64]float64) float64 {
	sum, k := 0.0, 0
	for _, m := range seedSets {
		if v := weighted(selectMetric(m, name), w); !math.IsNaN(v) {
			sum += v
			k++
		}
	}
	if k == 0 {
		return math.NaN()
	}
	return sum / float64(k)
}

func daysOf(sets ...*metricSeries) func(name string) []int64 {
	return func(name string) []int64 {
		var all []dayMean
		for _, m := range sets {
			all = append(all, selectMetric(m, name))
		}
		return allDays(all...)
	}
}

func sampleSD(xs []float64) float64 {
	if len(xs) < 2 {
		return math.NaN()
	}
	mean := 0.0
	for _, x := range xs {
		mean += x
	}
	mean /= float64(len(xs))
	ss := 0.0
	for _, x := range xs {
		ss += (x - mean) * (x - mean)
	}
	return math.Sqrt(ss / float64(len(xs)-1))
}

func buildEdgeReport(list []capture, units map[string][]edgeUnit, rawCount, dedupedCount map[string]int, seeds int) map[string]any {
	var captureNames []string
	for _, c := range list {
		captureNames = append(captureNames, c.file)
	}
	periods := append([]string{""}, edgePeriods...)
	view := func(period string) (a, b, d *metricSeries, c []*metricSeries) {
		a, b, d = collect(units["A"], period), collect(units["B"], period), collect(units["D"], period)
		for s := 1; s <= seeds; s++ {
			c = append(c, collect(units[fmt.Sprintf("C%02d", s)], period))
		}
		return
	}
	variantBlock := func(period string, name string, m *metricSeries) map[string]any {
		block := map[string]any{"n_ladder": m.n}
		for _, metric := range edgeMetrics {
			block[metric] = single(m, metric)
		}
		_ = name
		return block
	}
	out := map[string]any{
		"captures": captureNames, "seeds": seeds,
		"unit":      "one opportunity per level per 2 h, first sight across overlapping captures; mean R over filled trades with the XAU spread (oneR_cost, twoR_cost, ladder_cost); 95 % CI = symbol-day cluster bootstrap, 2000 draws",
		"published": rawCount, "after_dedupe": dedupedCount,
	}
	byPeriod := map[string]any{}
	paired := map[string]any{}
	for _, period := range periods {
		key := period
		if key == "" {
			key = "all"
		}
		a, b, d, c := view(period)
		block := map[string]any{"A": variantBlock(period, "A", a), "B": variantBlock(period, "B", b), "D": variantBlock(period, "D", d)}
		// C: per seed point means, the mean over seeds, the seed spread and the bootstrap of the seed mean
		cBlock := map[string]any{}
		for _, metric := range edgeMetrics {
			var perSeed []float64
			for _, m := range c {
				perSeed = append(perSeed, selectMetric(m, metric).mean())
			}
			cd := bootstrapDiff(daysOf(c...)(metric), func(w map[int64]float64) float64 { return seedMean(c, metric, w) })
			cBlock[metric] = map[string]any{"mean_over_seeds": cd.Mean, "ci95_lo": cd.Lo, "ci95_hi": cd.Hi, "seed_sd": fnum(sampleSD(perSeed))}
		}
		var cN []int
		for _, m := range c {
			cN = append(cN, m.n)
		}
		cBlock["n_ladder_per_seed"] = cN
		block["C"] = cBlock
		byPeriod[key] = block
		// paired differences on the metrics that matter
		diffs := map[string]any{}
		for _, metric := range []string{"oneR", "twoR", "ladder", "edge_ladder"} {
			am, bm, dm := selectMetric(a, metric), selectMetric(b, metric), selectMetric(d, metric)
			diffs["A-B_"+metric] = bootstrapDiff(allDays(am, bm), func(w map[int64]float64) float64 { return weighted(am, w) - weighted(bm, w) })
			diffs["A-C_"+metric] = bootstrapDiff(daysOf(append([]*metricSeries{a}, c...)...)(metric), func(w map[int64]float64) float64 { return weighted(am, w) - seedMean(c, metric, w) })
			diffs["D-A_"+metric] = bootstrapDiff(allDays(dm, am), func(w map[int64]float64) float64 { return weighted(dm, w) - weighted(am, w) })
		}
		paired[key] = diffs
	}
	out["by_period"] = byPeriod
	out["paired"] = paired
	return out
}
