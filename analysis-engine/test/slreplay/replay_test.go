package slreplay_test

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/sessionlevel"
)

// captures are the committed real multi-timeframe captures. Roles are fixed BEFORE
// results are read: xau-0921 (the profitable-week capture) is the development
// capture used while checking threshold magnitudes; every other capture is held
// out, chronologically separate from it. The XAU incident window (28 Sep - 6 Oct)
// contains the losing Session Level trades that led to the XAU containment, so it
// is NOT an independent validation of a design that was shaped by those losses: it
// is reported as "incident" and the FX captures are the held-out check. The "extended"
// XAU capture was taken on 12 Oct, after the design was fixed: its candles from
// 6 Oct 06:00 UTC on (after the last losing trade) are the out-of-sample XAU window;
// the part before it repeats the incident window.
var captures = []struct {
	File, Label, Role string
}{
	{"replay-xau-production-capture-20260921.json", "XAU 14-21 Sep (profit week)", "development"},
	{"replay-xau-m1-production-capture-20261006.json", "XAU 28 Sep-6 Oct (incident window)", "incident"},
	{"replay-xau-production-capture-20261012.json", "XAU 1-12 Oct (incident window, then out-of-sample)", "extended"},
	{"replay-eurusd-production-capture-20261006.json", "EURUSD 6 Oct", "held-out"},
	{"replay-gbpusd-production-capture-20261005.json", "GBPUSD 5 Oct", "held-out"},
	{"replay-gbpjpy-production-capture-20261006.json", "GBPJPY 6 Oct", "held-out"},
	{"replay-usdjpy-production-capture-20261005.json", "USDJPY 5 Oct", "held-out"},
}

// frozenParams adds the v3 leaves config/analysis.yml carried before v4 to the
// production parameters (the shared detector contract the engine injects).
func frozenParams(params map[string]any) map[string]any {
	out := make(map[string]any, len(params)+4)
	for k, v := range params {
		out[k] = v
	}
	out["invalidation_buffer_atr"] = 0.5
	out["minimum_target_distance_atr"] = 1.0
	out["expiry_hours"] = 24.0
	return out
}

// outOfSampleFrom is 6 Oct 06:00 UTC, after the last of the five losing trades.
const outOfSampleFrom = int64(1791266400)

// stageRank orders how far an episode got.
var stageRank = map[sessionlevel.State]int{
	sessionlevel.StateTouched: 1, sessionlevel.StateSwept: 1, sessionlevel.StateReclaimWaiting: 2,
	sessionlevel.StateReactionConfirm: 3, sessionlevel.StateCandidate: 4,
}

type episodeRecord struct {
	Key       string
	Direction market.Direction
	Level     string
	Model     string
	EpisodeAt int64
	Reached   int
	Final     sessionlevel.State
	Reason    string
	Published bool
	Rejected  bool // a reaction was confirmed but refused (envelope, floor, room...)
}

// v2Fact is what an independent audit of the candles says about the level a v2 setup
// traded, at the moment v2 published it. It never changes what v2 published.
type v2Fact struct {
	Level    string
	Price    float64
	Found    bool // the candle that made the extreme was located
	FormedAt int64
	AgeHours float64
	// Status is what v4 knew about that level at that moment: current, superseded
	// (a newer occurrence of the same name replaced it), incomplete:<reason> (its
	// defining period was not covered), no_current_level (the name had no level).
	Status string
	// SweptBefore: price traded beyond the level after it became available and before
	// the reaction's touch.
	SweptBefore bool
	// SweepTied: the most recent sweep before the confirmation was within the reclaim
	// window of it; SweptUntied is SweptBefore without that link.
	SweepTied   bool
	SweptUntied bool
	// Touches the level really had up to the observation (-1: the candles could not
	// show it). v2 claimed 2 whatever the truth was.
	Touches        int
	PeriodComplete bool
	AnyDefect      bool
}

// v4Fact is the independent audit of a published v4 setup: every v3 defect measured
// the same way, plus the production touch count against the independent one.
type v4Fact struct {
	Level          string
	Kind           string
	Model          string
	AgeHours       float64
	Status         string
	PriorTouches   int
	TouchesKnown   bool
	AuditedTouches int // -1 unknown
	SweptUntied    bool
	PeriodComplete bool
}

type runResult struct {
	Label, Role, Symbol     string
	Evaluations             int
	V2, V4                  []row
	V2Facts                 []v2Fact
	V4Facts                 []v4Fact
	Episodes                []episodeRecord
	Funnel                  map[string]int
	Terminal                map[string]int
	LevelsBuilt             map[string]int
	PeriodsRefused          map[string]int
	V2Unpublished           map[string]int
	Overlap                 int
	EngineOfflineMismatches int
}

func rowOf(version string, c opportunity.Candidate, observedAt int64, atr float64) position {
	return position{
		Version: version, ID: c.ID, StructuralID: c.StructuralID, Direction: c.Direction,
		EntryLow: c.Entry.Low, EntryHigh: c.Entry.High, Stop: float64(c.Invalidation.Price), Target: float64(c.Targets[0].Price.Price),
		ObservedAt: observedAt, ExpiresAt: observedAt + (c.ExpiresAt - c.CreatedAt), ATR: atr,
	}
}

func TestSessionLevelReplayCertification(t *testing.T) {
	dir := os.Getenv("SL_REPORT_DIR")
	if dir == "" {
		t.Skip("SL_REPORT_DIR not set (the replay certification takes minutes; see docs/strategies/session_level.md)")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	results := make([]runResult, len(captures))
	var wg sync.WaitGroup
	for i, c := range captures {
		wg.Add(1)
		go func(i int, file, label, role string) {
			defer wg.Done()
			res, err := runCapture(doc, file, label, role)
			if err != nil {
				t.Errorf("%s: %v", file, err)
				return
			}
			results[i] = res
		}(i, c.File, c.Label, c.Role)
	}
	wg.Wait()
	if t.Failed() {
		return
	}
	for _, res := range results {
		raw, _ := json.MarshalIndent(res, "", " ")
		if err := os.WriteFile(filepath.Join(dir, "rows-"+strings.ToLower(res.Symbol)+"-"+res.Role+".json"), raw, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	summary := render(results)
	if err := os.WriteFile(filepath.Join(dir, "summary.md"), []byte(summary), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Log("\n" + summary)
}

func sessionParamsFor(doc *config.Document, symbol string) (map[string]any, error) {
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		return nil, err
	}
	if err := engine.ApplyInstrument(&settings, doc, symbol); err != nil {
		return nil, err
	}
	for _, s := range settings.Strategies {
		if s.ID == sessionlevel.ID {
			return s.Parameters, nil
		}
	}
	return nil, fmt.Errorf("session_level is not configured")
}

// frozenInputs are the parameters the frozen v3 read: the production leaves of the
// shared detector contract, plus its own three.
func frozenInputs(params map[string]any) map[string]any { return frozenParams(params) }

func runCapture(doc *config.Document, file, label, role string) (runResult, error) {
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", file))
	if err != nil {
		return runResult{}, err
	}
	m5, err := capture.Candles(market.M5)
	if err != nil {
		return runResult{}, err
	}
	h1, err := capture.Candles(market.H1)
	if err != nil {
		return runResult{}, err
	}
	params, err := sessionParamsFor(doc, string(capture.Symbol))
	if err != nil {
		return runResult{}, err
	}
	instance, err := sessionlevel.New(strategy.Config{ID: sessionlevel.ID, Version: sessionlevel.Version, Enabled: true, Parameters: params})
	if err != nil {
		return runResult{}, err
	}
	v4 := instance.(*sessionlevel.Strategy)
	cfg, err := sessionlevel.ParseConfig(params)
	if err != nil {
		return runResult{}, err
	}
	v2i, err := newFrozenV2(strategy.Config{ID: "session_level", Version: "v3", Enabled: true, Parameters: frozenInputs(params)})
	if err != nil {
		return runResult{}, err
	}
	v2 := v2i
	index := make(map[int64]int, len(m5))
	for i, b := range m5 {
		index[b.Time] = i
	}
	res := runResult{
		Label: label, Role: role, Symbol: string(capture.Symbol),
		Funnel: map[string]int{}, Terminal: map[string]int{}, LevelsBuilt: map[string]int{}, PeriodsRefused: map[string]int{}, V2Unpublished: map[string]int{},
	}
	seen2, seen4 := map[string]bool{}, map[string]bool{}
	builtSeen, refusedSeen := map[string]bool{}, map[string]bool{}
	records := map[string]*episodeRecord{}
	var order []string
	mid := m5[len(m5)/2].Time
	if role == "extended" {
		mid = outOfSampleFrom
	}
	window := func(t int64) string {
		if t < mid {
			return "first"
		}
		return "second"
	}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		res.Evaluations++
		atr := 0.0
		if f := e.Context.Timeframes[market.M5]; f != nil && f.Legacy != nil {
			atr = f.Legacy.DetectorATR
		}
		analysis, candidates := v4.Analyze(e.Context)
		i := index[e.BarTime]
		// The engine's context and the offline detector read from the same candles
		// must reach the same decision (over the same trailing window).
		if ctxM5 := e.Context.Timeframes[market.M5]; ctxM5 != nil {
			n := len(ctxM5.Candles)
			from := i + 1 - n
			if from < 0 {
				from = 0
			}
			offline := sessionlevel.DetectAsOf(cfg, sessionlevel.Input{M5: m5[from : i+1], H1: h1}, i-from)
			if !sameDecision(offline, analysis) {
				res.EngineOfflineMismatches++
			}
		}
		for _, l := range analysis.Levels {
			if !builtSeen[l.ID] {
				builtSeen[l.ID] = true
				res.LevelsBuilt[l.Kind]++
			}
		}
		for _, r := range analysis.Rejected {
			k := fmt.Sprintf("%s|%d|%s", r.Name, r.Start, r.Reason)
			if !refusedSeen[k] {
				refusedSeen[k] = true
				res.PeriodsRefused[r.Reason]++
			}
		}
		for _, ep := range analysis.Episodes {
			rec := records[ep.Key()]
			if rec == nil {
				rec = &episodeRecord{Key: ep.Key(), Direction: ep.Direction, Level: ep.Level.Name, Model: ep.Model, EpisodeAt: ep.EpisodeAt}
				records[ep.Key()] = rec
				order = append(order, ep.Key())
			}
			if rank := stageRank[ep.State]; rank > rec.Reached {
				rec.Reached = rank
			}
			if ep.State == sessionlevel.StateCandidate {
				rec.Published = true
			}
			if ep.State == sessionlevel.StateReactionConfirm && ep.Reason != "" && ep.Reason != sessionlevel.ReasonConfirmationStale && ep.Reason != sessionlevel.ReasonSuperseded {
				rec.Rejected = true
			}
			rec.Final, rec.Reason = ep.State, ep.Reason
		}
		for _, c := range v2.Evaluate(e.Context) {
			if seen2[c.ID] {
				continue
			}
			seen2[c.ID] = true
			p := rowOf("v3", c, e.BarTime, atr)
			res.V2 = append(res.V2, row{position: p, Window: window(e.BarTime), Overall: c.Quality.Overall, Stars: stars(c)})
			res.V2Facts = append(res.V2Facts, auditV2(cfg, m5, h1, i, c, analysis))
		}
		for _, c := range candidates {
			if seen4[c.ID] {
				continue
			}
			seen4[c.ID] = true
			res.V4 = append(res.V4, row{position: rowOf("v4", c, e.BarTime, atr), Window: window(e.BarTime), Overall: c.Quality.Overall, Stars: stars(c)})
			res.V4Facts = append(res.V4Facts, auditV4(cfg, m5, h1, i, c, analysis))
		}
	}})
	if err != nil {
		return res, err
	}
	for i := range res.V2 {
		finish(&res.V2[i], m5)
	}
	for i := range res.V4 {
		finish(&res.V4[i], m5)
	}
	for _, k := range order {
		rec := records[k]
		res.Episodes = append(res.Episodes, *rec)
		res.Funnel["1_episodes"]++
		for stage, name := range map[int]string{2: "2_reclaim_or_late", 3: "3_reaction_confirmed"} {
			if rec.Reached >= stage || rec.Published {
				res.Funnel[name]++
			}
		}
		if rec.Published {
			res.Funnel["4_published"]++
			res.Terminal["published"]++
			continue
		}
		switch {
		case rec.Reason != "":
			res.Terminal[rec.Reason]++
		default:
			res.Terminal["in_progress_at_end:"+string(rec.Final)]++
		}
	}
	for i, r2 := range res.V2 {
		matched := false
		for _, r4 := range res.V4 {
			if r4.Direction == r2.Direction && math.Abs(float64(r4.ObservedAt-r2.ObservedAt)) <= 3600 && sameLevel(r4.StructuralID, res.V2Facts[i].Level) {
				matched = true
				break
			}
		}
		if matched {
			res.Overlap++
			continue
		}
		res.V2Unpublished[res.V2Facts[i].Status]++
	}
	return res, nil
}

// sameLevel reports whether a v4 structural id is about the named level.
func sameLevel(structuralID, level string) bool {
	return strings.Contains(structuralID, ":level:"+level+":")
}

// sameDecision compares the technical decision of the engine-built context with the
// offline one read from the capture's candles: the same levels and the same
// episodes in the same states. The engine additionally applies the confluence
// floor, which turns an offline CANDIDATE into a confirmed-but-refused reaction.
func sameDecision(offline, analysed sessionlevel.Analysis) bool {
	if len(offline.Levels) != len(analysed.Levels) || len(offline.Episodes) != len(analysed.Episodes) {
		return false
	}
	for i := range offline.Levels {
		if offline.Levels[i] != analysed.Levels[i] {
			return false
		}
	}
	got := map[string]sessionlevel.Episode{}
	for _, ep := range analysed.Episodes {
		got[ep.Key()] = ep
	}
	for _, off := range offline.Episodes {
		on, ok := got[off.Key()]
		if !ok {
			return false
		}
		if off.State == on.State && off.Reason == on.Reason {
			continue
		}
		candidateClass := func(e sessionlevel.Episode) bool {
			return e.State == sessionlevel.StateCandidate ||
				e.State == sessionlevel.StateReactionConfirm && (e.Reason == sessionlevel.ReasonConfluenceFloor || e.Reason == sessionlevel.ReasonSuperseded)
		}
		if candidateClass(off) && candidateClass(on) {
			continue
		}
		return false
	}
	return true
}

func render(results []runResult) string {
	var b strings.Builder
	fmt.Fprintf(&b, "# Session Level replay certification\n\nHypothetical outcomes under causal fill rules (see outcome_test.go); never realised performance. M5 evaluations replay every committed capture bar by bar through the production engine. v3 is the frozen Python-parity publisher (levels from the engine frame, any reclaim, a hard-coded touch count of 2).\n\n")
	for _, res := range results {
		fmt.Fprintf(&b, "## %s [%s]\n\n- evaluations: %d (engine vs offline decision mismatches: %d)\n- v3 setups also published by v4 (same direction, within an hour, same level): %d of %d\n", res.Label, res.Role, res.Evaluations, res.EngineOfflineMismatches, res.Overlap, len(res.V2))
		fmt.Fprintf(&b, "- v3: %s\n", fmtStats(summarise(res.V2, nil)))
		fmt.Fprintf(&b, "- v4: %s\n", fmtStats(summarise(res.V4, nil)))
		fmt.Fprintf(&b, "- Quality.Overall mix (stars/3): v3 [%s], v4 [%s]\n", starMix(res.V2), starMix(res.V4))
		for _, w := range []string{"first", "second"} {
			f := func(r row) bool { return r.Window == w }
			fmt.Fprintf(&b, "  - %s half v3: %s\n  - %s half v4: %s\n", w, fmtStats(summarise(res.V2, f)), w, fmtStats(summarise(res.V4, f)))
		}
		tally := tallyV2(res.V2Facts)
		fmt.Fprintf(&b, "- v3 setups audited against the v4 contract: %d located; level not current in v4 %d (superseded %d, period incomplete %d, no current level %d); level swept before the reaction and the reclaim not tied to it %d; touches claimed 2 but measured differently %d (measured 0: %d, 1: %d, 2: %d, 3+: %d, unknown: %d); older than 24 h %d; any defect %d (median level age %.1f h)\n",
			tally.found, tally.notCurrent, tally.superseded, tally.incomplete, tally.noLevel, tally.swept, tally.touchMismatch, tally.t0, tally.t1, tally.t2, tally.t3, tally.tUnknown, tally.old, tally.any, tally.medianAge)
		fmt.Fprintf(&b, "- v4 levels built (distinct): %s; periods refused (distinct): %s\n- v4 funnel (distinct episodes): %s\n- v4 how episodes ended: %s\n- v3 setups v4 did not publish, by v4's status of the level: %s\n\n", sortedCounts(res.LevelsBuilt), sortedCounts(res.PeriodsRefused), sortedCounts(res.Funnel), sortedCounts(res.Terminal), sortedCounts(res.V2Unpublished))
	}
	b.WriteString(pooled(results))
	return b.String()
}

type v2Tally struct {
	found, notCurrent, superseded, incomplete, noLevel, swept, touchMismatch, t0, t1, t2, t3, tUnknown, old, any int
	medianAge                                                                                                    float64
}

func tallyV2(facts []v2Fact) v2Tally {
	var t v2Tally
	var ages []float64
	for _, f := range facts {
		if !f.Found {
			continue
		}
		t.found++
		ages = append(ages, f.AgeHours)
		switch {
		case f.Status == "current":
		case f.Status == "superseded":
			t.notCurrent++
			t.superseded++
		case strings.HasPrefix(f.Status, "incomplete"):
			t.notCurrent++
			t.incomplete++
		default:
			t.notCurrent++
			t.noLevel++
		}
		if f.SweptUntied {
			t.swept++
		}
		switch {
		case f.Touches < 0:
			t.tUnknown++
		case f.Touches == 0:
			t.t0++
		case f.Touches == 1:
			t.t1++
		case f.Touches == 2:
			t.t2++
		default:
			t.t3++
		}
		if f.Touches != 2 {
			t.touchMismatch++
		}
		if f.AgeHours > 24 {
			t.old++
		}
		if f.AnyDefect {
			t.any++
		}
	}
	sort.Float64s(ages)
	t.medianAge = math.NaN()
	if len(ages) > 0 {
		t.medianAge = ages[len(ages)/2]
	}
	return t
}

// rValues are the realised R of the filled rows (a hypothetical result).
func rValues(rows []row, keep func(i int) bool) []float64 {
	var out []float64
	for i, r := range rows {
		if keep != nil && !keep(i) {
			continue
		}
		switch r.Out.Result {
		case "target", "stop", "timeout":
			out = append(out, r.Out.R)
		}
	}
	return out
}

// bootstrapCI is a seeded percentile bootstrap of the mean (95%).
func bootstrapCI(values []float64) (mean, lo, hi float64) {
	n := len(values)
	if n == 0 {
		return math.NaN(), math.NaN(), math.NaN()
	}
	sum := 0.0
	for _, v := range values {
		sum += v
	}
	mean = sum / float64(n)
	if n < 2 {
		return mean, math.NaN(), math.NaN()
	}
	state := uint64(0x9E3779B97F4A7C15)
	next := func() uint64 {
		state ^= state << 13
		state ^= state >> 7
		state ^= state << 17
		return state
	}
	const resamples = 2000
	means := make([]float64, resamples)
	for r := range means {
		total := 0.0
		for i := 0; i < n; i++ {
			total += values[int(next()%uint64(n))]
		}
		means[r] = total / float64(n)
	}
	sort.Float64s(means)
	return mean, means[int(0.025*resamples)], means[int(0.975*resamples)]
}

func fmtR(values []float64) string {
	if len(values) == 0 {
		return "n=0"
	}
	stops, targets := 0, 0
	for _, v := range values {
		if v <= -0.999 {
			stops++
		}
		if v > 0 {
			targets++
		}
	}
	m, lo, hi := bootstrapCI(values)
	return fmt.Sprintf("filled n=%d, mean R %.2f [95%% bootstrap %.2f, %.2f], stopped %d, positive %d", len(values), m, lo, hi, stops, targets)
}

// pooled groups the replays by role and compares what v3 published, the part of it
// the v4 contract would have refused, the part it would not, and v4.
func pooled(results []runResult) string {
	var b strings.Builder
	fmt.Fprintf(&b, "## Pooled by role\n\nThe v4 thresholds were checked for magnitude on the development capture only. The incident window holds the XAU losses that motivated the rebuild, so it is reported separately and is NOT independent evidence. The four FX captures are the held-out check. \"Defective\" means the v3 setup traded a level that fails at least one v4 requirement (not current, swept before the reaction with a reclaim not tied to that sweep, or older than a day); \"clean\" means it meets all of them; \"unlocated\" are v3 setups whose level could not be found in the candles. Hypothetical outcomes only.\n\n")
	for _, role := range []string{"development", "incident", "held-out", "post-incident XAU"} {
		var v2, v4 []row
		var defective, clean, unlocated []float64
		for _, res := range results {
			match := res.Role == role
			after := func(r row) bool { return true }
			if role == "post-incident XAU" {
				match = res.Role == "extended"
				after = func(r row) bool { return r.ObservedAt >= outOfSampleFrom }
			}
			if !match {
				continue
			}
			facts := res.V2Facts
			keep := func(i int) bool { return after(res.V2[i]) }
			for _, r := range res.V2 {
				if after(r) {
					v2 = append(v2, r)
				}
			}
			for _, r := range res.V4 {
				if after(r) {
					v4 = append(v4, r)
				}
			}
			defective = append(defective, rValues(res.V2, func(i int) bool { return keep(i) && facts[i].Found && facts[i].AnyDefect })...)
			clean = append(clean, rValues(res.V2, func(i int) bool { return keep(i) && facts[i].Found && !facts[i].AnyDefect })...)
			unlocated = append(unlocated, rValues(res.V2, func(i int) bool { return keep(i) && !facts[i].Found })...)
		}
		fmt.Fprintf(&b, "### %s\n\n- v3 (all setups): published %d; %s\n- v3 defective vs the v4 contract: %s\n- v3 clean: %s\n- v3 unlocated: %s\n- v4: published %d; %s\n\n",
			role, len(v2), fmtR(rValues(v2, nil)), fmtR(defective), fmtR(clean), fmtR(unlocated), len(v4), fmtR(rValues(v4, nil)))
	}
	return b.String()
}

func stars(c opportunity.Candidate) int {
	if c.DetectorConfluence == nil {
		return 0
	}
	return c.DetectorConfluence.SelectedStars
}

// starMix is how many rows carry each confluence star count (Quality.Overall = stars/3).
func starMix(rows []row) string {
	counts := map[int]int{}
	for _, r := range rows {
		counts[r.Stars]++
	}
	return fmt.Sprintf("1 star %d, 2 stars %d, 3 stars %d", counts[1], counts[2], counts[3])
}
