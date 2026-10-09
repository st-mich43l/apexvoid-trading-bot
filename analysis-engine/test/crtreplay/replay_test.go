package crtreplay_test

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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/crt"
)

// captures are the committed real multi-timeframe captures. Roles are fixed
// BEFORE results are read: xau-0921 (the profitable-week capture) was the only
// capture used while sanity-checking threshold magnitudes; every other capture
// is held out.
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

type row struct {
	position
	Anchor           int64
	Window           string // first | second half of the capture timeline
	Out              outcome
	PlannedRR        float64
	RiskPips         float64
	RewardATR        float64
	ZoneATR          float64
	StopInsideWick   bool
	BarsSweepToShift int
}

type rejection struct {
	Reason     string
	Direction  market.Direction
	AnchorTime int64
	SweepTime  int64
}

type runResult struct {
	Label, Role, Symbol string
	Evaluations         int
	V2, V3              []row
	Rejections          map[string]int // unique episodes by reason
	V2OnlyReasons       map[string]int
	Overlap, V2Only     int
	V3Only              int
	// NoEpisode breaks down the v2 setups for which v3 saw no sweep episode at
	// all (reason no_v3_episode): why the sweep v2 accepted is not one.
	NoEpisode map[string]int
	// EngineOfflineMismatches counts M5 evaluations where the engine's context
	// and the offline capture gave different CRT technical decisions (want 0).
	EngineOfflineMismatches int
}

func TestCRTReplayCertification(t *testing.T) {
	dir := os.Getenv("CRT_REPORT_DIR")
	if dir == "" {
		t.Skip("CRT_REPORT_DIR not set (the replay certification takes minutes; see docs/strategies/crt.md)")
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
		path := filepath.Join(dir, "rows-"+strings.ToLower(res.Symbol)+"-"+res.Role+".json")
		raw, _ := json.MarshalIndent(res, "", " ")
		if err := os.WriteFile(path, raw, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	summary := render(results)
	if err := os.WriteFile(filepath.Join(dir, "summary.md"), []byte(summary), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Log("\n" + summary)
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
		if s.ID == crt.ID {
			params = s.Parameters
		}
	}
	instance, err := crt.New(strategy.Config{ID: crt.ID, Version: crt.Version, Enabled: true, Parameters: params})
	if err != nil {
		return runResult{}, err
	}
	v3 := instance.(*crt.Strategy)
	cfg, err := crt.ParseConfig(params)
	if err != nil {
		return runResult{}, err
	}
	v2, err := newFrozenV2(params)
	if err != nil {
		return runResult{}, err
	}
	h1, err := capture.Candles(market.H1)
	if err != nil {
		return runResult{}, err
	}
	index := make(map[int64]int, len(m5))
	for i, b := range m5 {
		index[b.Time] = i
	}
	res := runResult{Label: label, Role: role, Symbol: string(capture.Symbol), Rejections: map[string]int{}, V2OnlyReasons: map[string]int{}, NoEpisode: map[string]int{}}
	seen2, seen3 := map[string]bool{}, map[string]bool{}
	seenRej := map[rejection]bool{}
	var rej []rejection
	mid := m5[len(m5)/2].Time
	window := func(t int64) string {
		if t < mid {
			return "first"
		}
		return "second"
	}
	mkRow := func(c position, anchor int64, bars int) row {
		return row{position: c, Anchor: anchor, Window: window(c.ObservedAt), BarsSweepToShift: bars}
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
			res.V2 = append(res.V2, mkRow(p, anchorOf(c.StructuralID), 0))
		}
		analysis, candidates := v3.Analyze(e.Context)
		// The engine-built context must give the same technical decision as the
		// capture's candles read offline with the future present.
		if offline := crt.DetectAsOf(cfg, crt.Input{H1: h1, M5: m5}, index[e.BarTime]); !sameEpisodes(offline, analysis) {
			res.EngineOfflineMismatches++
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
			bars := 0
			if setup.Shift != nil {
				bars = int((setup.Shift.BarTime - setup.Sweep.BarTime) / 300)
			}
			res.V3 = append(res.V3, mkRow(p, setup.Anchor.OpenTime, bars))
		}
		for _, r := range analysis.Rejections {
			key := rejection{Reason: r.Reason, Direction: r.Direction, AnchorTime: r.AnchorTime, SweepTime: r.SweepTime}
			if !seenRej[key] {
				seenRej[key] = true
				rej = append(rej, key)
				res.Rejections[r.Reason]++
			}
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
	// Overlap: same anchor and direction.
	v3Keys := map[string]bool{}
	for _, r := range res.V3 {
		v3Keys[r.StructuralID] = true
	}
	v2Keys := map[string]bool{}
	for _, r := range res.V2 {
		v2Keys[r.StructuralID] = true
		if v3Keys[r.StructuralID] {
			continue
		}
		reason := "no_v3_episode"
		for _, x := range rej {
			if x.AnchorTime == r.Anchor && sideOf(x.Direction) == sideOf(r.Direction) {
				reason = x.Reason
				break
			}
		}
		res.V2OnlyReasons[reason]++
		if reason == "no_v3_episode" {
			res.NoEpisode[classifyNoEpisode(r, m5, h1, cfg)]++
		}
	}
	for k := range v2Keys {
		if v3Keys[k] {
			res.Overlap++
		} else {
			res.V2Only++
		}
	}
	for k := range v3Keys {
		if !v2Keys[k] {
			res.V3Only++
		}
	}
	return res, nil
}

// classifyNoEpisode says why the sweep a v2 setup relied on is not a CRT sweep
// under v3, measured from the candles: the market never pierced the anchor edge
// after the anchor closed; pierced it by less than the sweep threshold; or only
// pierced it later than the one-hour sweep window.
func classifyNoEpisode(r row, m5, h1 []market.Candle, cfg crt.Config) string {
	var high, low float64
	found := false
	for _, c := range h1 {
		if c.Time == r.Anchor {
			high, low, found = c.High, c.Low, true
		}
	}
	if !found {
		return "anchor_not_found"
	}
	closeTime := r.Anchor + 3600
	threshold := math.Max(cfg.MinimumSweepPips*cfg.PipSize, cfg.MinimumSweepATR*r.ATR)
	maxPenetration, firstRaid := 0.0, int64(0)
	for _, b := range m5 {
		if b.Time < closeTime || b.Time > r.ObservedAt {
			continue
		}
		penetration := low - b.Low
		if r.Direction == market.Sell {
			penetration = b.High - high
		}
		maxPenetration = math.Max(maxPenetration, penetration)
		if penetration > threshold && firstRaid == 0 {
			firstRaid = b.Time
		}
	}
	switch {
	case maxPenetration <= 0:
		return "no_penetration_after_anchor_close"
	case firstRaid == 0:
		return "sub_threshold_penetration"
	case firstRaid >= closeTime+3600:
		return "sweep_after_one_hour_window"
	default:
		return "other"
	}
}

func sideOf(d market.Direction) string { return strings.ToLower(string(d)) }

func anchorOf(structuralID string) int64 {
	parts := strings.Split(structuralID, ":")
	v, _ := strconv.ParseInt(parts[len(parts)-1], 10, 64)
	return v
}

// finish simulates the position and derives its technical measurements.
func finish(r *row, bars []market.Candle) {
	r.Out = simulate(bars, r.position)
	prox := r.proximal()
	risk, reward := math.Abs(prox-r.Stop), math.Abs(r.Target-prox)
	if risk > 0 {
		r.PlannedRR = reward / risk
	}
	pip := 0.0
	// pip size is inferred from price scale: the replay symbols are XAU 0.1,
	// JPY pairs 0.01, others 0.0001.
	switch {
	case prox > 1000:
		pip = 0.1
	case prox > 50:
		pip = 0.01
	default:
		pip = 0.0001
	}
	r.RiskPips = risk / pip
	if r.ATR > 0 {
		r.RewardATR = reward / r.ATR
		r.ZoneATR = (r.EntryHigh - r.EntryLow) / r.ATR
	}
	// Is the stop beyond the actual manipulation extreme (deepest point since
	// the anchor closed through the observation)?
	closeTime := r.Anchor + 3600
	extreme := math.Inf(1)
	if r.Direction == market.Sell {
		extreme = math.Inf(-1)
	}
	for _, b := range bars {
		if b.Time < closeTime || b.Time > r.ObservedAt {
			continue
		}
		if r.Direction == market.Sell {
			extreme = math.Max(extreme, b.High)
		} else {
			extreme = math.Min(extreme, b.Low)
		}
	}
	if !math.IsInf(extreme, 0) {
		if r.Direction == market.Sell {
			r.StopInsideWick = r.Stop <= extreme
		} else {
			r.StopInsideWick = r.Stop >= extreme
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
	N, Filled, Target, Stop, Timeout, Unfilled, Ambiguous, InsideWick int
	MeanR, MedianRR, MedianRiskPips, MedianRewardATR, MedianZoneATR   float64
	MeanMFE, MeanMAE                                                  float64
	MedianBarsToFill                                                  float64
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
		if r.StopInsideWick {
			s.InsideWick++
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
	return fmt.Sprintf("n=%d filled=%d (target %d / stop %d / timeout %d, same-bar ambiguous %d) unfilled=%d meanR=%.2f MFE=%.2fR MAE=%.2fR | planned RR med %.2f, risk med %.1f pips, reward med %.1f ATR, zone med %.2f ATR, median bars-to-fill %.0f, stop inside sweep wick %d",
		s.N, s.Filled, s.Target, s.Stop, s.Timeout, s.Ambiguous, s.Unfilled, s.MeanR, s.MeanMFE, s.MeanMAE, s.MedianRR, s.MedianRiskPips, s.MedianRewardATR, s.MedianZoneATR, s.MedianBarsToFill, s.InsideWick)
}

func render(results []runResult) string {
	var b strings.Builder
	fmt.Fprintf(&b, "# CRT replay certification\n\nHypothetical outcomes under causal fill rules (see outcome_test.go); never realised performance. M5 evaluations replay every committed capture bar by bar through the production engine.\n\n")
	for _, res := range results {
		fmt.Fprintf(&b, "## %s [%s]\n\n- evaluations: %d (engine vs offline CRT decision mismatches: %d)\n- overlap (same anchor+side): %d, v2-only: %d, v3-only: %d\n", res.Label, res.Role, res.Evaluations, res.EngineOfflineMismatches, res.Overlap, res.V2Only, res.V3Only)
		fmt.Fprintf(&b, "- v2: %s\n", fmtStats(summarise(res.V2, nil)))
		fmt.Fprintf(&b, "- v3: %s\n", fmtStats(summarise(res.V3, nil)))
		for _, w := range []string{"first", "second"} {
			f := func(r row) bool { return r.Window == w }
			fmt.Fprintf(&b, "  - %s half v2: %s\n  - %s half v3: %s\n", w, fmtStats(summarise(res.V2, f)), w, fmtStats(summarise(res.V3, f)))
		}
		fmt.Fprintf(&b, "- v3 rejections (unique episodes): %s\n- v2 setups v3 did not publish, by v3's reason: %s\n- of those, v3 saw no episode because: %s\n\n", sortedCounts(res.Rejections), sortedCounts(res.V2OnlyReasons), sortedCounts(res.NoEpisode))
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

// sameEpisodes compares the technical episodes two analyses found: the set of
// setups together with the setups the confluence floor then refused (Analyze
// moves those from Setups to Rejections), against the offline detector's raw
// setups.
func sameEpisodes(offline, analysed crt.Analysis) bool {
	key := func(dir market.Direction, anchor, sweep int64) string {
		return fmt.Sprintf("%s/%d/%d", dir, anchor, sweep)
	}
	want := map[string]bool{}
	for _, s := range offline.Setups {
		want[key(s.Direction, s.Anchor.OpenTime, s.Sweep.BarTime)] = true
	}
	got := map[string]bool{}
	for _, s := range analysed.Setups {
		got[key(s.Direction, s.Anchor.OpenTime, s.Sweep.BarTime)] = true
	}
	for _, r := range analysed.Rejections {
		if r.Reason == crt.ReasonConfluenceBelowFloor {
			got[key(r.Direction, r.AnchorTime, r.SweepTime)] = true
		}
	}
	if len(want) != len(got) {
		return false
	}
	for k := range want {
		if !got[k] {
			return false
		}
	}
	return true
}
