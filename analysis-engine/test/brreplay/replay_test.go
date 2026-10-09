package brreplay_test

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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/breakretest"
)

// captures are the committed real multi-timeframe captures. Roles are fixed
// BEFORE results are read: xau-0921 (the profitable-week capture) was the only
// capture used while sanity-checking threshold magnitudes; every other capture
// is held out, chronologically separate from it.
var captures = []struct {
	File, Label, Role string
}{
	{"replay-xau-production-capture-20260921.json", "XAU 14-21 Sep (profit week)", "development"},
	{"replay-xau-m1-production-capture-20261006.json", "XAU 28 Sep-6 Oct (incident window)", "held-out"},
	{"replay-eurusd-production-capture-20261006.json", "EURUSD 6 Oct", "held-out"},
	{"replay-gbpusd-production-capture-20261005.json", "GBPUSD 5 Oct", "held-out"},
	{"replay-gbpjpy-production-capture-20261006.json", "GBPJPY 6 Oct", "held-out"},
	{"replay-usdjpy-production-capture-20261005.json", "USDJPY 5 Oct", "held-out"},
}

// stageRank orders how far an episode got; terminal states keep the rank it had
// reached, which is recorded separately as Reached.
var stageRank = map[breakretest.State]int{
	breakretest.StateBreakPending: 1, breakretest.StateBreakAccepted: 2, breakretest.StateRetestWaiting: 2,
	breakretest.StateRetestTouched: 3, breakretest.StateRetestConfirmed: 4, breakretest.StateCandidate: 5,
}

// episodeRecord is one distinct break attempt as seen across the whole replay.
type episodeRecord struct {
	Key       string
	Direction market.Direction
	Kind      string
	BreakAt   int64
	Reached   int
	Final     breakretest.State
	Reason    string
	Published bool
	// Rejected means a retest was confirmed but refused (envelope, floor, room...).
	Rejected bool
}

type row struct {
	position
	Kind              string
	Window            string // first | second half of the capture timeline
	Out               outcome
	PlannedRR         float64
	RiskPips          float64
	RewardATR         float64
	ZoneATR           float64
	StopInsideRecent  bool    // stop inside the range of the 12 candles before the observation
	RetestAgeBars     float64 // v2 key-level path: candles between its retest candle and the observation
	BreakDisp         float64 // v3
	BarsAcceptConfirm int     // v3
	Touches           int     // v3
}

type runResult struct {
	Label, Role, Symbol string
	Evaluations         int
	V2, V3              []row
	Episodes            []episodeRecord
	// Funnel counts distinct episodes by the furthest stage they reached.
	Funnel map[string]int
	// Terminal counts distinct episodes by their final reason.
	Terminal map[string]int
	// V2Unpublished explains each v2 setup v3 did not publish, by v3's reason.
	V2Unpublished map[string]int
	Overlap       int
	// EngineOfflineMismatches counts M5 evaluations where the engine's context
	// and the capture's candles read offline gave different technical decisions.
	EngineOfflineMismatches int
}

func TestBreakRetestReplayCertification(t *testing.T) {
	dir := os.Getenv("BR_REPORT_DIR")
	if dir == "" {
		t.Skip("BR_REPORT_DIR not set (the replay certification takes minutes; see docs/strategies/break_retest.md)")
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

// frozenParams adds the v2 leaves config/analysis.yml carried before v3 to the
// production parameters (the shared detector contract the engine injects).
func frozenParams(params map[string]any) map[string]any {
	out := make(map[string]any, len(params)+8)
	for k, v := range params {
		out[k] = v
	}
	out["breakout_accept_bars"] = 2.0
	out["trendline_tolerance_atr"] = 0.30
	out["momentum_body_fraction"] = 0.60
	out["strict_premium_discount"] = false
	out["invalidation_buffer_atr"] = 0.25
	out["target_r"] = 2.0
	out["expiry_hours"] = 8.0
	return out
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
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		return runResult{}, err
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		return runResult{}, err
	}
	var params map[string]any
	for _, s := range settings.Strategies {
		if s.ID == breakretest.ID {
			params = s.Parameters
		}
	}
	instance, err := breakretest.New(strategy.Config{ID: breakretest.ID, Version: breakretest.Version, Enabled: true, Parameters: params})
	if err != nil {
		return runResult{}, err
	}
	v3 := instance.(*breakretest.Strategy)
	cfg, err := breakretest.ParseConfig(params)
	if err != nil {
		return runResult{}, err
	}
	v2, err := newFrozenV2(strategy.Config{ID: "break_retest", Version: "v2", Enabled: true, Parameters: frozenParams(params)})
	if err != nil {
		return runResult{}, err
	}
	index := make(map[int64]int, len(m5))
	for i, b := range m5 {
		index[b.Time] = i
	}
	res := runResult{Label: label, Role: role, Symbol: string(capture.Symbol), Funnel: map[string]int{}, Terminal: map[string]int{}, V2Unpublished: map[string]int{}}
	seen2, seen3 := map[string]bool{}, map[string]bool{}
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
		for _, c := range v2.Evaluate(e.Context) {
			if seen2[c.ID] {
				continue
			}
			seen2[c.ID] = true
			p := position{
				Version: "v2", ID: c.ID, StructuralID: c.StructuralID, Direction: c.Direction,
				EntryLow: c.Entry.Low, EntryHigh: c.Entry.High, Stop: float64(c.Invalidation.Price), Target: float64(c.Targets[0].Price.Price),
				ObservedAt: e.BarTime, ExpiresAt: e.BarTime + (c.ExpiresAt - c.CreatedAt), ATR: atr,
			}
			kind := "trendline"
			if strings.Contains(c.StructuralID, "level") || strings.HasPrefix(c.StructuralID, "reaction") || strings.HasPrefix(c.StructuralID, "round") {
				kind = "key_level"
			}
			r := row{position: p, Kind: kind, Window: window(e.BarTime)}
			if kind == "key_level" {
				// v2 FormedAt for the key-level path is the retest candle itself.
				r.RetestAgeBars = float64(c.CreatedAt-c.FormedAt) / 300
			}
			res.V2 = append(res.V2, r)
		}
		analysis, candidates := v3.Analyze(e.Context)
		if offline := breakretest.DetectAsOf(cfg, m5, index[e.BarTime]); !sameDecision(offline, analysis) {
			res.EngineOfflineMismatches++
		}
		for _, ep := range analysis.Episodes {
			rec := records[ep.Key()]
			if rec == nil {
				rec = &episodeRecord{Key: ep.Key(), Direction: ep.Direction, Kind: ep.Reference.Kind, BreakAt: ep.BreakStart}
				records[ep.Key()] = rec
				order = append(order, ep.Key())
			}
			if rank := stageRank[ep.State]; rank > rec.Reached {
				rec.Reached = rank
			}
			if ep.State == breakretest.StateCandidate {
				rec.Published = true
			}
			if ep.State == breakretest.StateRetestConfirmed && ep.Reason != "" && ep.Reason != breakretest.ReasonConfirmationStale && ep.Reason != breakretest.ReasonSuperseded {
				rec.Rejected = true
			}
			rec.Final, rec.Reason = ep.State, ep.Reason
		}
		for i, c := range candidates {
			if seen3[c.ID] {
				continue
			}
			seen3[c.ID] = true
			setup := analysis.Setups[i]
			p := position{
				Version: "v3", ID: c.ID, StructuralID: c.StructuralID, Direction: c.Direction,
				EntryLow: c.Entry.Low, EntryHigh: c.Entry.High, Stop: float64(c.Invalidation.Price), Target: float64(c.Targets[0].Price.Price),
				ObservedAt: e.BarTime, ExpiresAt: e.BarTime + (c.ExpiresAt - c.CreatedAt), ATR: atr,
			}
			res.V3 = append(res.V3, row{
				position: p, Kind: setup.Reference.Kind, Window: window(e.BarTime), BreakDisp: setup.Break.DisplacementATR,
				BarsAcceptConfirm: int((setup.ConfirmedAt - setup.Break.AcceptedAt) / 300), Touches: setup.Reference.Touches,
			})
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
		res.Funnel["1_break_started"]++
		switch {
		case rec.Published:
			res.Funnel["5_published"]++
		}
		for stage, name := range map[int]string{2: "2_break_accepted", 3: "3_retest_touched", 4: "4_retest_confirmed"} {
			if rec.Reached >= stage || rec.Published {
				res.Funnel[name]++
			}
		}
		switch {
		case rec.Published:
			res.Terminal["published"]++
		case rec.Reason != "":
			res.Terminal[rec.Reason]++
		default:
			res.Terminal["in_progress_at_end:"+string(rec.Final)]++
		}
	}
	// Each v2 setup against v3: published (an event of the same direction within
	// an hour with an overlapping entry band) or refused for v3's own reason.
	for _, r2 := range res.V2 {
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
		reason := "no_v3_episode"
		best := int64(math.MaxInt64)
		for _, rec := range res.Episodes {
			if rec.Direction != r2.Direction {
				continue
			}
			if d := absDiff(rec.BreakAt, r2.ObservedAt); d <= 4*3600 && d < best {
				best = d
				reason = rec.Reason
				if rec.Reason == "" {
					reason = "in_progress:" + string(rec.Final)
				}
			}
		}
		res.V2Unpublished[reason]++
	}
	return res, nil
}

func absDiff(a, b int64) int64 {
	if a > b {
		return a - b
	}
	return b - a
}

// finish simulates the position and derives its technical measurements.
func finish(r *row, bars []market.Candle) {
	r.Out = simulate(bars, r.position)
	prox := r.proximal()
	risk, reward := math.Abs(prox-r.Stop), math.Abs(r.Target-prox)
	if risk > 0 {
		r.PlannedRR = reward / risk
	}
	pip := 0.0001
	switch {
	case prox > 1000:
		pip = 0.1
	case prox > 50:
		pip = 0.01
	}
	r.RiskPips = risk / pip
	if r.ATR > 0 {
		r.RewardATR = reward / r.ATR
		r.ZoneATR = (r.EntryHigh - r.EntryLow) / r.ATR
	}
	// Is the stop inside the range of the 12 candles before the observation (the
	// swing the retest just made)? A stop there is inside the noise it must survive.
	lo, hi := math.Inf(1), math.Inf(-1)
	for _, b := range bars {
		if b.Time > r.ObservedAt-12*300 && b.Time <= r.ObservedAt {
			lo, hi = math.Min(lo, b.Low), math.Max(hi, b.High)
		}
	}
	if !math.IsInf(lo, 0) {
		if r.Direction == market.Sell {
			r.StopInsideRecent = r.Stop <= hi
		} else {
			r.StopInsideRecent = r.Stop >= lo
		}
	}
}

func median(values []float64) float64 {
	if len(values) == 0 {
		return math.NaN()
	}
	sort.Float64s(values)
	return values[len(values)/2]
}

type stats struct {
	N, Filled, Target, Stop, Timeout, Unfilled, Ambiguous, InsideRecent int
	MeanR, MedianRR, MedianRiskPips, MedianRewardATR, MedianZoneATR     float64
	MeanMFE, MeanMAE, MedianBarsToFill                                  float64
}

func summarise(rows []row, filter func(row) bool) stats {
	var s stats
	var rs, rrs, risks, rewards, zones, mfe, mae, fillBars []float64
	for _, r := range rows {
		if filter != nil && !filter(r) {
			continue
		}
		s.N++
		rrs, risks, rewards, zones = append(rrs, r.PlannedRR), append(risks, r.RiskPips), append(rewards, r.RewardATR), append(zones, r.ZoneATR)
		if r.StopInsideRecent {
			s.InsideRecent++
		}
		switch r.Out.Result {
		case "unfilled", "invalid":
			s.Unfilled++
			continue
		case "target":
			s.Target++
		case "stop":
			s.Stop++
		case "timeout":
			s.Timeout++
		}
		s.Filled++
		if r.Out.SameBarAmbiguous {
			s.Ambiguous++
		}
		rs, mfe, mae, fillBars = append(rs, r.Out.R), append(mfe, r.Out.MFE), append(mae, r.Out.MAE), append(fillBars, float64(r.Out.BarsToFill))
	}
	mean := func(v []float64) float64 {
		if len(v) == 0 {
			return math.NaN()
		}
		sum := 0.0
		for _, x := range v {
			sum += x
		}
		return sum / float64(len(v))
	}
	s.MeanR, s.MeanMFE, s.MeanMAE = mean(rs), mean(mfe), mean(mae)
	s.MedianRR, s.MedianRiskPips, s.MedianRewardATR, s.MedianZoneATR = median(rrs), median(risks), median(rewards), median(zones)
	s.MedianBarsToFill = median(fillBars)
	return s
}

func fmtStats(s stats) string {
	if s.N == 0 {
		return "n=0"
	}
	return fmt.Sprintf("n=%d filled=%d (target %d / stop %d / timeout %d, same-bar ambiguous %d) unfilled=%d meanR=%.2f MFE=%.2fR MAE=%.2fR | planned RR med %.2f, risk med %.1f pips, reward med %.1f ATR, zone med %.2f ATR, median bars-to-fill %.0f, stop inside the last-12-candle range %d",
		s.N, s.Filled, s.Target, s.Stop, s.Timeout, s.Ambiguous, s.Unfilled, s.MeanR, s.MeanMFE, s.MeanMAE, s.MedianRR, s.MedianRiskPips, s.MedianRewardATR, s.MedianZoneATR, s.MedianBarsToFill, s.InsideRecent)
}

func render(results []runResult) string {
	var b strings.Builder
	fmt.Fprintf(&b, "# Break & Retest replay certification\n\nHypothetical outcomes under causal fill rules (see outcome_test.go); never realised performance. M5 evaluations replay every committed capture bar by bar through the production engine.\n\n")
	for _, res := range results {
		fmt.Fprintf(&b, "## %s [%s]\n\n- evaluations: %d (engine vs offline decision mismatches: %d)\n- v2 setups also published by v3 (same direction, within an hour, overlapping entry band): %d of %d\n", res.Label, res.Role, res.Evaluations, res.EngineOfflineMismatches, res.Overlap, len(res.V2))
		fmt.Fprintf(&b, "- v2: %s\n", fmtStats(summarise(res.V2, nil)))
		fmt.Fprintf(&b, "- v3: %s\n", fmtStats(summarise(res.V3, nil)))
		for _, w := range []string{"first", "second"} {
			f := func(r row) bool { return r.Window == w }
			fmt.Fprintf(&b, "  - %s half v2: %s\n  - %s half v3: %s\n", w, fmtStats(summarise(res.V2, f)), w, fmtStats(summarise(res.V3, f)))
		}
		var ages []float64
		for _, r := range res.V2 {
			if r.Kind == "key_level" {
				ages = append(ages, r.RetestAgeBars)
			}
		}
		stale := 0
		for _, a := range ages {
			if a > 2 {
				stale++
			}
		}
		fmt.Fprintf(&b, "- v2 key-level setups whose retest candle was more than 2 candles old when published: %d of %d (median age %.0f candles)\n", stale, len(ages), median(ages))
		fmt.Fprintf(&b, "- v3 funnel (distinct episodes): %s\n- v3 how episodes ended: %s\n- v2 setups v3 did not publish, by v3's reason: %s\n\n", sortedCounts(res.Funnel), sortedCounts(res.Terminal), sortedCounts(res.V2Unpublished))
	}
	return b.String()
}

func sortedCounts(m map[string]int) string {
	if len(m) == 0 {
		return "none"
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, len(keys))
	for i, k := range keys {
		parts[i] = fmt.Sprintf("%s=%d", k, m[k])
	}
	return strings.Join(parts, ", ")
}

// sameDecision compares the technical decision of the engine-built context with
// the offline one read from the capture's candles: the same episodes in the same
// states. The engine additionally applies the confluence floor, which turns an
// offline CANDIDATE into a confirmed-but-refused retest.
func sameDecision(offline, analysed breakretest.Analysis) bool {
	if len(offline.Episodes) != len(analysed.Episodes) {
		return false
	}
	got := map[string]breakretest.Episode{}
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
		// The engine applies the confluence floor while choosing among references
		// broken in the same move; the offline decision does not. Both describe the
		// same confirmed retest, so these outcomes are one class.
		candidateClass := func(e breakretest.Episode) bool {
			return e.State == breakretest.StateCandidate ||
				e.State == breakretest.StateRetestConfirmed && (e.Reason == breakretest.ReasonConfluenceFloor || e.Reason == breakretest.ReasonSuperseded)
		}
		if candidateClass(off) && candidateClass(on) {
			continue
		}
		return false
	}
	return true
}
