package ifvgreplay_test

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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/ifvg"
)

// captures are the committed real multi-timeframe captures. Roles are fixed BEFORE
// results are read: xau-0921 (the profitable-week capture) is the development
// capture used while checking threshold magnitudes; every other capture is held
// out, chronologically separate from it. The XAU incident window (28 Sep - 6 Oct)
// contains the losing iFVG trades that led to the XAU containment, so it is NOT an
// independent validation of a design that was shaped by those losses: it is reported
// as "incident" and the FX captures are the held-out check.
var captures = []struct {
	File, Label, Role string
}{
	{"replay-xau-production-capture-20260921.json", "XAU 14-21 Sep (profit week)", "development"},
	{"replay-xau-m1-production-capture-20261006.json", "XAU 28 Sep-6 Oct (incident window)", "incident"},
	{"replay-eurusd-production-capture-20261006.json", "EURUSD 6 Oct", "held-out"},
	{"replay-gbpusd-production-capture-20261005.json", "GBPUSD 5 Oct", "held-out"},
	{"replay-gbpjpy-production-capture-20261006.json", "GBPJPY 6 Oct", "held-out"},
	{"replay-usdjpy-production-capture-20261005.json", "USDJPY 5 Oct", "held-out"},
}

// frozenParams adds the v2 leaves config/analysis.yml carried before v3 to the
// production parameters (the shared detector contract the engine injects).
func frozenParams(params map[string]any) map[string]any {
	out := make(map[string]any, len(params)+8)
	for k, v := range params {
		out[k] = v
	}
	out["minimum_strength"] = 0.4
	out["minimum_gap_atr"] = 0.5
	out["invalidation_buffer_atr"] = 0.5
	out["minimum_target_distance_atr"] = 1.0
	out["expiry_hours"] = 24.0
	return out
}

// stageRank orders how far an episode got; terminal states keep the rank it had
// reached, which is recorded separately.
var stageRank = map[ifvg.State]int{
	ifvg.StateInversionConfirmed: 1, ifvg.StateRetestWaiting: 1, ifvg.StateRetestTouched: 2,
	ifvg.StateRejectionConfirmed: 3, ifvg.StateCandidate: 4,
}

type episodeRecord struct {
	Key         string
	Direction   market.Direction
	GapFormedAt int64
	InvertedAt  int64
	Reached     int
	Final       ifvg.State
	Reason      string
	Published   bool
	// Rejected means a rejection was confirmed but refused (envelope, floor, room...).
	Rejected bool
}

// v2Facts are the technical facts of one published v2 setup, rebuilt offline from
// the capture's candles with the v3 thresholds applied to what v2 actually traded.
type v2Facts struct {
	GapFormedAt            int64
	Found                  bool
	GapATR                 float64
	InvertBeyondATR        float64
	WeakGap                bool // the gap is narrower than minimum_gap_atr
	WeakOrigin             bool // the middle candle is not a displacement
	InversionInsideBuffer  bool // the inverting close finished inside the v3 buffer
	InversionQualityFails  bool // the inverting candle misses v3's body, close location or displacement
	TouchNotAfterInversion bool // the reaction's touch is not strictly after the inversion
	StaleGap               bool // inverted more than maximum_inversion_bars after forming
	ConfirmDelayBars       float64
	AnyV3Defect            bool
}

type runResult struct {
	Label, Role, Symbol     string
	Evaluations             int
	V2Resting               int
	V2, V3                  []row
	V2Facts                 []v2Facts
	V3Facts                 []v2Facts
	Episodes                []episodeRecord
	Funnel                  map[string]int
	Terminal                map[string]int
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

func TestIFVGReplayCertification(t *testing.T) {
	dir := os.Getenv("IFVG_REPORT_DIR")
	if dir == "" {
		t.Skip("IFVG_REPORT_DIR not set (the replay certification takes minutes; see docs/strategies/ifvg.md)")
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

func ifvgParamsFor(doc *config.Document, symbol string) (map[string]any, error) {
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		return nil, err
	}
	if err := engine.ApplyInstrument(&settings, doc, symbol); err != nil {
		return nil, err
	}
	for _, s := range settings.Strategies {
		if s.ID == ifvg.ID {
			return s.Parameters, nil
		}
	}
	return nil, fmt.Errorf("ifvg is not configured")
}

func runCapture(doc *config.Document, file, label, role string) (runResult, error) {
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", file))
	if err != nil {
		return runResult{}, err
	}
	m5, err := capture.Candles(market.M5)
	if err != nil {
		return runResult{}, err
	}
	params, err := ifvgParamsFor(doc, string(capture.Symbol))
	if err != nil {
		return runResult{}, err
	}
	instance, err := ifvg.New(strategy.Config{ID: ifvg.ID, Version: ifvg.Version, Enabled: true, Parameters: params})
	if err != nil {
		return runResult{}, err
	}
	v3 := instance.(*ifvg.Strategy)
	cfg, err := ifvg.ParseConfig(params)
	if err != nil {
		return runResult{}, err
	}
	v2, err := newFrozenV2(strategy.Config{ID: "ifvg", Version: "v2", Enabled: true, Parameters: frozenParams(params)})
	if err != nil {
		return runResult{}, err
	}
	index := make(map[int64]int, len(m5))
	for i, b := range m5 {
		index[b.Time] = i
	}
	res := runResult{Label: label, Role: role, Symbol: string(capture.Symbol), Funnel: map[string]int{}, Terminal: map[string]int{}, V2Unpublished: map[string]int{}}
	seen2, seen3, restingSeen := map[string]bool{}, map[string]bool{}, map[string]bool{}
	records := map[string]*episodeRecord{}
	var order []string
	mid := m5[len(m5)/2].Time
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
		resting, confirmed := v2.Evaluate(e.Context)
		for _, c := range resting {
			if !restingSeen[c.ID] {
				restingSeen[c.ID] = true
				res.V2Resting++
			}
		}
		for _, c := range confirmed {
			if seen2[c.ID] {
				continue
			}
			seen2[c.ID] = true
			p := rowOf("v2", c, e.BarTime, atr)
			res.V2 = append(res.V2, row{position: p, Window: window(e.BarTime), Overall: c.Quality.Overall, Stars: stars(c)})
			res.V2Facts = append(res.V2Facts, classifyV2(m5, index, cfg, c))
		}
		analysis, candidates := v3.Analyze(e.Context)
		if offline := ifvg.DetectAsOf(cfg, m5, index[e.BarTime]); !sameDecision(offline, analysis) {
			res.EngineOfflineMismatches++
		}
		for _, ep := range analysis.Episodes {
			rec := records[ep.Key()]
			if rec == nil {
				rec = &episodeRecord{Key: ep.Key(), Direction: ep.Direction, GapFormedAt: ep.Gap.FormedAt, InvertedAt: ep.InvertedAt}
				records[ep.Key()] = rec
				order = append(order, ep.Key())
			}
			if rank := stageRank[ep.State]; rank > rec.Reached {
				rec.Reached = rank
			}
			if ep.State == ifvg.StateCandidate {
				rec.Published = true
			}
			if ep.State == ifvg.StateRejectionConfirmed && ep.Reason != "" && ep.Reason != ifvg.ReasonConfirmationStale && ep.Reason != ifvg.ReasonSuperseded {
				rec.Rejected = true
			}
			rec.Final, rec.Reason = ep.State, ep.Reason
		}
		for _, c := range candidates {
			if seen3[c.ID] {
				continue
			}
			seen3[c.ID] = true
			res.V3 = append(res.V3, row{position: rowOf("v3", c, e.BarTime, atr), Window: window(e.BarTime), ConfirmDelayBars: float64(c.CreatedAt-c.FormedAt) / 300, Overall: c.Quality.Overall, Stars: stars(c)})
			res.V3Facts = append(res.V3Facts, classifyV3(m5, index, cfg, c))
		}
	}})
	if err != nil {
		return res, err
	}
	for i := range res.V2 {
		finish(&res.V2[i], m5)
	}
	for i := range res.V3 {
		finish(&res.V3[i], m5)
	}
	for _, k := range order {
		rec := records[k]
		res.Episodes = append(res.Episodes, *rec)
		res.Funnel["1_inverted"]++
		for stage, name := range map[int]string{2: "2_retest_touched", 3: "3_rejection_confirmed"} {
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
	byGap := map[string]episodeRecord{}
	for _, rec := range res.Episodes {
		key := fmt.Sprintf("%s:%d", rec.Direction, rec.GapFormedAt)
		best, seen := byGap[key]
		if !seen || (rec.Published && !best.Published) || (rec.Published == best.Published && rec.Reached > best.Reached) {
			byGap[key] = rec
		}
	}
	for i, r2 := range res.V2 {
		matched := false
		for _, r3 := range res.V3 {
			if r3.Direction == r2.Direction && math.Abs(float64(r3.ObservedAt-r2.ObservedAt)) <= 3600 &&
				r3.EntryLow <= r2.EntryHigh && r2.EntryLow <= r3.EntryHigh {
				matched = true
				break
			}
		}
		if matched {
			res.Overlap++
			continue
		}
		f := res.V2Facts[i]
		reason := "v2_gap_not_rebuilt"
		switch {
		case !f.Found:
		default:
			if rec, ok := byGap[fmt.Sprintf("%s:%d", r2.Direction, f.GapFormedAt)]; ok {
				reason = rec.Reason
				if rec.Published {
					reason = "published_other_confirmation"
				} else if reason == "" {
					reason = "in_progress:" + string(rec.Final)
				}
			} else {
				switch {
				case f.StaleGap:
					reason = "gap_inverted_too_late"
				case f.WeakGap:
					reason = "gap_below_minimum"
				case f.WeakOrigin:
					reason = "weak_origin_displacement"
				default:
					reason = "no_v3_episode"
				}
			}
		}
		res.V2Unpublished[reason]++
	}
	return res, nil
}

// classifyV2 rebuilds the gap and inversion v2 traded from the capture's candles and
// measures them against the v3 contract. It never changes what v2 published.
func classifyV2(m5 []market.Candle, index map[int64]int, cfg ifvg.Config, c opportunity.Candidate) v2Facts {
	var f v2Facts
	buy := c.Direction == market.Buy
	obs, ok := index[c.CreatedAt]
	if !ok {
		return f
	}
	// Find the gap whose band v2 entered: the most recent formation candle whose gap
	// bound equals the entry band's proximal-opposite edge.
	formed := -1
	for i := obs; i >= 2; i-- {
		a, third := m5[i-2], m5[i]
		if buy && a.Low > third.High && math.Abs(third.High-c.Entry.Low) < 1e-6 {
			formed = i
			break
		}
		if !buy && a.High < third.Low && math.Abs(a.High-c.Entry.Low) < 1e-6 {
			formed = i
			break
		}
	}
	if formed < 0 {
		return f
	}
	return classifyGap(m5, cfg, buy, formed, obs, c.FormedAt, c.CreatedAt)
}

// classifyV3 measures a published v3 setup the same way, from the gap identity its
// structural id carries (technique:ifvg:<side>:fvg:<role>:<formedAt>:<invertedAt>).
func classifyV3(m5 []market.Candle, index map[int64]int, cfg ifvg.Config, c opportunity.Candidate) v2Facts {
	var side, role string
	var formedAt, invertedAt int64
	if _, err := fmt.Sscanf(strings.ReplaceAll(c.StructuralID, ":", " "), "technique ifvg %s fvg %s %d %d", &side, &role, &formedAt, &invertedAt); err != nil {
		return v2Facts{}
	}
	formed, ok1 := index[formedAt]
	obs, ok2 := index[c.CreatedAt]
	if !ok1 || !ok2 {
		return v2Facts{}
	}
	return classifyGap(m5, cfg, c.Direction == market.Buy, formed, obs, c.Reaction.TouchBarTime, c.CreatedAt)
}

func classifyGap(m5 []market.Candle, cfg ifvg.Config, buy bool, formed, obs int, touchAt, confirmedAt int64) v2Facts {
	var f v2Facts
	atrAt := func(i int) float64 {
		start := i - cfg.ATRWindowBars + 1
		if start < 0 {
			start = 0
		}
		series, ok := indicator.WilderATR(m5[start:i+1], cfg.ATRLength)
		if !ok {
			return 0
		}
		return series[len(series)-1]
	}
	var gapLow, gapHigh float64
	a, third := m5[formed-2], m5[formed]
	if buy {
		gapLow, gapHigh = third.High, a.Low
	} else {
		gapLow, gapHigh = a.High, third.Low
	}
	invert := -1
	for i := formed + 1; i <= obs; i++ {
		if (buy && m5[i].Close > gapHigh) || (!buy && m5[i].Close < gapLow) {
			invert = i
			break
		}
	}
	if invert < 0 {
		return f
	}
	f.Found = true
	f.GapFormedAt = m5[formed].Time
	atrC, atrPre, atrJ := atrAt(formed), atrAt(invert-1), atrAt(invert)
	if atrC <= 0 || atrPre <= 0 || atrJ <= 0 {
		f.Found = false
		return f
	}
	width := gapHigh - gapLow
	f.GapATR = width / atrC
	f.WeakGap = width < math.Max(cfg.MinimumGapPips*cfg.PipSize, cfg.MinimumGapATR*atrC)
	b := m5[formed-1]
	originBody := b.Open - b.Close
	if !buy {
		originBody = b.Close - b.Open
	}
	rng := math.Max(b.High-b.Low, 1e-12)
	f.WeakOrigin = !(originBody > 0) || originBody/atrC < cfg.MinimumOriginBodyATR || originBody/rng < cfg.MinimumOriginBody
	j := m5[invert]
	beyond := j.Close - gapHigh
	body := j.Close - j.Open
	strength := (j.Close - j.Low) / math.Max(j.High-j.Low, 1e-12)
	if !buy {
		beyond = gapLow - j.Close
		body = j.Open - j.Close
		strength = (j.High - j.Close) / math.Max(j.High-j.Low, 1e-12)
	}
	f.InvertBeyondATR = beyond / atrJ
	f.InversionInsideBuffer = beyond <= math.Max(cfg.MinimumBreakPips*cfg.PipSize, cfg.MinimumBreakATR*atrPre)
	f.InversionQualityFails = !(body > 0) || body/math.Max(j.High-j.Low, 1e-12) < cfg.MinInversionBody || strength < cfg.MinInversionCloseStr || body/atrJ < cfg.MinInversionDisp
	f.TouchNotAfterInversion = touchAt <= j.Time
	f.StaleGap = int((j.Time-m5[formed].Time)/300) > cfg.MaximumInversionBars
	f.ConfirmDelayBars = float64(confirmedAt-j.Time) / 300
	f.AnyV3Defect = f.WeakGap || f.WeakOrigin || f.InversionInsideBuffer || f.InversionQualityFails || f.TouchNotAfterInversion || f.StaleGap
	return f
}

// sameDecision compares the technical decision of the engine-built context with
// the offline one read from the capture's candles: the same episodes in the same
// states. The engine additionally applies the confluence floor, which turns an
// offline CANDIDATE into a confirmed-but-refused rejection.
func sameDecision(offline, analysed ifvg.Analysis) bool {
	if len(offline.Episodes) != len(analysed.Episodes) {
		return false
	}
	got := map[string]ifvg.Episode{}
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
		candidateClass := func(e ifvg.Episode) bool {
			return e.State == ifvg.StateCandidate ||
				e.State == ifvg.StateRejectionConfirmed && (e.Reason == ifvg.ReasonConfluenceFloor || e.Reason == ifvg.ReasonSuperseded)
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
	fmt.Fprintf(&b, "# iFVG replay certification\n\nHypothetical outcomes under causal fill rules (see outcome_test.go); never realised performance. M5 evaluations replay every committed capture bar by bar through the production engine. v2 is the frozen Python-parity publisher (confirmed reactions; resting observations are counted separately because the Algo Bot cannot execute them).\n\n")
	for _, res := range results {
		fmt.Fprintf(&b, "## %s [%s]\n\n- evaluations: %d (engine vs offline decision mismatches: %d)\n- v2 resting inversion observations (not executable): %d\n- v2 confirmed setups also published by v3 (same direction, within an hour, overlapping entry band): %d of %d\n", res.Label, res.Role, res.Evaluations, res.EngineOfflineMismatches, res.V2Resting, res.Overlap, len(res.V2))
		fmt.Fprintf(&b, "- v2: %s\n", fmtStats(summarise(res.V2, nil)))
		fmt.Fprintf(&b, "- v3: %s\n", fmtStats(summarise(res.V3, nil)))
		fmt.Fprintf(&b, "- Quality.Overall mix (stars/3): v2 [%s], v3 [%s]\n", starMix(res.V2), starMix(res.V3))
		for _, w := range []string{"first", "second"} {
			f := func(r row) bool { return r.Window == w }
			fmt.Fprintf(&b, "  - %s half v2: %s\n  - %s half v3: %s\n", w, fmtStats(summarise(res.V2, f)), w, fmtStats(summarise(res.V3, f)))
		}
		tally := tallyV2(res.V2Facts)
		fmt.Fprintf(&b, "- v2 setups rebuilt offline: %d of %d; against the v3 contract: weak gap %d, weak origin displacement %d, inversion inside the buffer %d, inversion candle below the quality bars %d, touch not after the inversion %d, stale gap %d; any defect %d (median confirmation delay %.0f candles)\n",
			tally.found, len(res.V2Facts), tally.weakGap, tally.weakOrigin, tally.insideBuffer, tally.quality, tally.touch, tally.stale, tally.any, tally.medianDelay)
		fmt.Fprintf(&b, "- v3 funnel (distinct inversion episodes): %s\n- v3 how episodes ended: %s\n- v2 setups v3 did not publish, by v3's reason: %s\n\n", sortedCounts(res.Funnel), sortedCounts(res.Terminal), sortedCounts(res.V2Unpublished))
	}
	b.WriteString(pooled(results))
	return b.String()
}

type v2Tally struct {
	found, weakGap, weakOrigin, insideBuffer, quality, touch, stale, any int
	medianDelay                                                          float64
}

func tallyV2(facts []v2Facts) v2Tally {
	var t v2Tally
	var delays []float64
	for _, f := range facts {
		if !f.Found {
			continue
		}
		t.found++
		delays = append(delays, f.ConfirmDelayBars)
		if f.WeakGap {
			t.weakGap++
		}
		if f.WeakOrigin {
			t.weakOrigin++
		}
		if f.InversionInsideBuffer {
			t.insideBuffer++
		}
		if f.InversionQualityFails {
			t.quality++
		}
		if f.TouchNotAfterInversion {
			t.touch++
		}
		if f.StaleGap {
			t.stale++
		}
		if f.AnyV3Defect {
			t.any++
		}
	}
	sort.Float64s(delays)
	t.medianDelay = math.NaN()
	if len(delays) > 0 {
		t.medianDelay = delays[len(delays)/2]
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

// pooled groups the replays by role and compares what v2 published, the part of it
// that the v3 contract would have refused, the part it would not, and v3.
func pooled(results []runResult) string {
	var b strings.Builder
	fmt.Fprintf(&b, "## Pooled by role\n\nThe v3 thresholds were checked for magnitude on the development capture only. The incident window holds the XAU losses that motivated the rebuild, so it is reported separately and is NOT independent evidence. The four FX captures are the held-out check. \"Defective\" means the v2 setup misses at least one v3 contract requirement as measured offline from the capture's candles; \"clean\" means it meets all of them; \"unrebuilt\" are v2 setups whose gap could not be located. Hypothetical outcomes only.\n\n")
	for _, role := range []string{"development", "incident", "held-out"} {
		var v2, v3 []row
		var defective, clean, unrebuilt []float64
		var all2 []float64
		for _, res := range results {
			if res.Role != role {
				continue
			}
			v2 = append(v2, res.V2...)
			v3 = append(v3, res.V3...)
			facts := res.V2Facts
			defective = append(defective, rValues(res.V2, func(i int) bool { return facts[i].Found && facts[i].AnyV3Defect })...)
			clean = append(clean, rValues(res.V2, func(i int) bool { return facts[i].Found && !facts[i].AnyV3Defect })...)
			unrebuilt = append(unrebuilt, rValues(res.V2, func(i int) bool { return !facts[i].Found })...)
		}
		all2 = rValues(v2, nil)
		fmt.Fprintf(&b, "### %s\n\n- v2 (all confirmed setups): published %d; %s\n- v2 defective vs the v3 contract: %s\n- v2 clean: %s\n- v2 unrebuilt: %s\n- v3: published %d; %s\n\n",
			role, len(v2), fmtR(all2), fmtR(defective), fmtR(clean), fmtR(unrebuilt), len(v3), fmtR(rValues(v3, nil)))
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
