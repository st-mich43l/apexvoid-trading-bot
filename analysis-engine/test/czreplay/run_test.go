package czreplay_test

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/confluencezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// Capture is one committed real production capture.
type Capture struct {
	Name     string // key in the legacy baseline
	Symbol   string
	File     string
	Baseline string
}

var captures = []Capture{
	{"XAU Sep", "XAU", "replay-xau-production-capture-20260921.json", "XAUSEP"},
	{"XAU Oct", "XAU", "replay-xau-production-capture-20261013.json", "XAU"},
	{"EURUSD", "EURUSD", "replay-eurusd-production-capture-20261013.json", "EURUSD"},
	{"GBPUSD", "GBPUSD", "replay-gbpusd-production-capture-20261013.json", "GBPUSD"},
	{"GBPJPY", "GBPJPY", "replay-gbpjpy-production-capture-20261013.json", "GBPJPY"},
	{"USDJPY", "USDJPY", "replay-usdjpy-production-capture-20261013.json", "USDJPY"},
}

// RejectRecord is a considered v3 setup that was not published, first seen at
// EvalTime and still on record at later evaluations.
type RejectRecord struct {
	Direction   string  `json:"direction"`
	Reason      string  `json:"reason"`
	EpisodeID   string  `json:"episode"`
	Low         float64 `json:"low"`
	High        float64 `json:"high"`
	TouchTime   int64   `json:"touch_time,omitempty"`
	ConfirmTime int64   `json:"confirm_time,omitempty"`
	FirstSeen   int64   `json:"first_seen_eval_time"`
}

// Run is everything one replay of one capture produced.
type Run struct {
	Capture     Capture
	Bars        []market.Candle
	Idx         map[int64]int
	Pip         float64
	StopMinPips float64
	StopMaxPips float64
	Evaluations int

	Decisions map[string][]*Decision // by model: A, B, C, D

	FactIDs, VerifiedFactIDs map[string]bool
	RegionEpisodes           map[string]bool
	ConfirmedSetups          map[string]bool // v3 setups before the confluence floor
	Rejections               map[string]*RejectRecord
	CausalityRejectedC       int

	OtherCandidates int
	OthersSHA       string
	// Snapshots are the strategy's full account at the evaluations of interest
	// (the evaluations around each XAU trade's confirmation).
	Snapshots map[int64]Snapshot
	// Inconsistent counts evaluations at which the strategy's own Analyze and the
	// engine's published candidates disagreed (must be zero).
	Inconsistent int
}

// Snapshot is one evaluation's complete v3 account and the engine's published
// confluence candidates.
type Snapshot struct {
	Analysis  confluencezone.Analysis
	Published []opportunity.Candidate
	B         *Decision
}

// snapshotTimes are the evaluations kept in full: from each XAU production trade's
// confirmation candle to three candles later (the engine can first see a
// confirmation up to its lookback after it).
func snapshotTimes(t testing.TB, symbol string) map[int64]bool {
	out := map[int64]bool{}
	for _, tr := range loadLedger(t) {
		if tr.Symbol != symbol {
			continue
		}
		confirm := utc(tr.AEConfirm[:16])
		for k := int64(0); k <= 3; k++ {
			out[confirm+300*k] = true
		}
	}
	return out
}

var (
	runsMu sync.Mutex
	runs   = map[string]*Run{}
)

type analyzer interface {
	Analyze(*analysiscontext.MarketContext) (confluencezone.Analysis, []opportunity.Candidate)
}

type baselineFile struct {
	RegimeEnd int64 `json:"regime_end_utc"`
	Captures  map[string]struct {
		Decisions []struct {
			Bar          int64   `json:"bar"`
			Direction    string  `json:"direction"`
			EntryLow     float64 `json:"entry_low"`
			EntryHigh    float64 `json:"entry_high"`
			Invalidation float64 `json:"invalidation"`
			Target       float64 `json:"target"`
			Pattern      string  `json:"pattern"`
			Touch        int64   `json:"touch"`
			Confirm      int64   `json:"confirm"`
		} `json:"decisions"`
	} `json:"captures"`
}

func loadBaseline(t testing.TB) baselineFile {
	t.Helper()
	raw, err := os.ReadFile(testdata("confluence-zone-legacy-intersection-baseline.json"))
	if err != nil {
		t.Fatal(err)
	}
	var b baselineFile
	if err := json.Unmarshal(raw, &b); err != nil {
		t.Fatal(err)
	}
	return b
}

// replay runs one capture through the production engine path once and caches it.
func replay(t testing.TB, c Capture) *Run {
	runsMu.Lock()
	defer runsMu.Unlock()
	if r, ok := runs[c.File]; ok {
		return r
	}
	r := doReplay(t, c, replayOpts{})
	runs[c.File] = r
	return r
}

// runAll replays every capture, in parallel (each replay is independent and
// deterministic), and returns them by capture name.
func runAll(t *testing.T) map[string]*Run {
	t.Helper()
	runsMu.Lock()
	cached := len(runs) == len(captures)
	runsMu.Unlock()
	if !cached {
		var wg sync.WaitGroup
		results := make([]*Run, len(captures))
		for i, c := range captures {
			wg.Add(1)
			go func(i int, c Capture) {
				defer wg.Done()
				results[i] = doReplay(t, c, replayOpts{})
			}(i, c)
		}
		wg.Wait()
		runsMu.Lock()
		for i, c := range captures {
			if results[i] != nil {
				runs[c.File] = results[i]
			}
		}
		runsMu.Unlock()
	}
	out := map[string]*Run{}
	runsMu.Lock()
	defer runsMu.Unlock()
	for _, c := range captures {
		r, ok := runs[c.File]
		if !ok {
			t.Fatalf("replay of %s did not complete", c.File)
		}
		out[c.Name] = r
	}
	return out
}

// replayOpts vary a replay: a cut-off, parameter overrides for Confluence Zone,
// and a light mode that keeps only the v3 strategy's own decisions.
type replayOpts struct {
	cut       *int64
	overrides map[string]any
	light     bool
}

func doReplay(t testing.TB, c Capture, o replayOpts) *Run {
	capture := loadCapture(t, c.File)
	if o.cut != nil {
		capture = truncate(capture, *o.cut)
	}
	return doReplayCapture(t, c, capture, o)
}

// doReplayCapture replays the given (possibly altered) capture of c.
func doReplayCapture(t testing.TB, c Capture, capture *replaycapture.Capture, o replayOpts) *Run {
	doc := loadDoc(t)
	settings := symbolSettings(t, doc, c.Symbol)
	params := strategyParams(t, settings, "confluence_zone")
	if len(o.overrides) > 0 {
		merged := make(map[string]any, len(params)+len(o.overrides))
		for k, v := range params {
			merged[k] = v
		}
		for k, v := range o.overrides {
			merged[k] = v
		}
		params = merged
	}
	legacy, err := strategyutil.ParseLegacyDetectorSettings(params)
	if err != nil {
		t.Fatal(err)
	}
	strat, err := confluencezone.New(strategy.Config{ID: confluencezone.ID, Version: confluencezone.Version, Enabled: true, Parameters: params})
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := confluencezone.ParseConfig(params)
	if err != nil {
		t.Fatal(err)
	}
	m5, err := capture.Candles(market.M5)
	if err != nil {
		t.Fatal(err)
	}
	r := &Run{
		Capture: c, Bars: m5, Idx: map[int64]int{}, Pip: legacy.PipSize,
		StopMinPips: settings.InstrumentStopMinPips, StopMaxPips: settings.InstrumentStopMaxPips,
		Snapshots: map[int64]Snapshot{}, Decisions: map[string][]*Decision{}, FactIDs: map[string]bool{}, VerifiedFactIDs: map[string]bool{},
		RegionEpisodes: map[string]bool{}, ConfirmedSetups: map[string]bool{}, Rejections: map[string]*RejectRecord{},
	}
	for i, b := range m5 {
		r.Idx[b.Time] = i
	}
	want := snapshotTimes(t, c.Symbol)
	fingerprint := strategyutil.Fingerprint("confluence_zone", "v2", map[string]any{"frozen": true})
	seen := map[string]bool{}
	var others []string
	// The frozen v2 reproduction (models B and C) reads the engine's technique instances, which the order_block v6 definition filters; this certification is about
	// Confluence Zone v3 and the other strategies as they stood, so the definition is removed (its own effect is measured in test/fvgdefinition).
	options := replaycapture.Options{}
	if len(o.overrides) > 0 {
		options.StrategyParameters = map[string]map[string]any{"confluence_zone": o.overrides}
	}
	options.OnEvaluation = func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		r.Evaluations++
		add := func(d *Decision) {
			if d == nil || seen[d.Key] {
				return
			}
			seen[d.Key] = true
			d.Symbol = c.Symbol
			r.Decisions[d.Model] = append(r.Decisions[d.Model], d)
		}
		if o.light {
			for _, cand := range e.Candidates {
				if string(cand.Strategy) == "confluence_zone" && cand.Reaction != nil {
					add(decisionFromCandidate(cand, e.BarTime, e.Context, r.Pip))
				}
			}
			return
		}
		b := modelB(e.Context, legacy, fingerprint, r.Pip)
		add(b)
		cs, rejected := modelC(e.Context, legacy, fingerprint, r.Pip, cfg.MinimumOverlapPips, cfg.MinimumOverlapATR)
		r.CausalityRejectedC += rejected
		for _, d := range cs {
			add(d)
		}
		analysis, candidates := strat.(analyzer).Analyze(e.Context)
		if want[e.BarTime] {
			r.Snapshots[e.BarTime] = Snapshot{Analysis: analysis, Published: candidates, B: b}
		}
		for _, f := range analysis.Facts {
			r.FactIDs[f.ID] = true
			if f.Verified {
				r.VerifiedFactIDs[f.ID] = true
			}
		}
		for _, region := range analysis.Regions {
			r.RegionEpisodes[region.EpisodeID] = true
		}
		for _, s := range analysis.Setups {
			r.ConfirmedSetups[fmt.Sprintf("%s|%d", s.Region.EpisodeID, s.ConfirmTime)] = true
		}
		for _, rej := range analysis.Rejections {
			key := fmt.Sprintf("%s|%s|%s|%d|%.5f|%.5f", rej.Direction, rej.Reason, rej.EpisodeID, rej.ConfirmTime, rej.Low, rej.High)
			if _, ok := r.Rejections[key]; !ok {
				r.Rejections[key] = &RejectRecord{
					Direction: string(rej.Direction), Reason: rej.Reason, EpisodeID: rej.EpisodeID, Low: rej.Low, High: rej.High,
					TouchTime: rej.TouchTime, ConfirmTime: rej.ConfirmTime, FirstSeen: e.BarTime,
				}
			}
		}
		// The strategy's own account and the engine's published candidates must agree.
		fromEngine, fromAnalyze := map[string]bool{}, map[string]bool{}
		for _, cand := range e.Candidates {
			if string(cand.Strategy) != "confluence_zone" {
				others = append(others, canonical(e.BarTime, cand))
				continue
			}
			if cand.Reaction != nil {
				fromEngine[cand.ID] = true
				add(decisionFromCandidate(cand, e.BarTime, e.Context, r.Pip))
			}
		}
		for _, cand := range candidates {
			if cand.Reaction != nil {
				fromAnalyze[cand.ID] = true
			}
		}
		if len(fromEngine) != len(fromAnalyze) {
			r.Inconsistent++
		}
		for id := range fromEngine {
			if !fromAnalyze[id] {
				r.Inconsistent++
			}
		}
	}
	_, err = replaycapture.Replay(doc, capture, market.M5, options)
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(others)
	sum := sha256.Sum256([]byte(strings.Join(others, "\n")))
	r.OtherCandidates, r.OthersSHA = len(others), hex.EncodeToString(sum[:])
	if !o.light {
		r.addModelA(t)
	}
	r.simulateAll()
	return r
}

func canonical(bar int64, c opportunity.Candidate) string {
	return fmt.Sprintf("%d|%s|%s|%s|%.6f|%.6f|%.6f|%.6f|%.6f|%v", bar, c.Strategy, c.ID, c.Direction, c.Entry.Low, c.Entry.High,
		float64(c.Invalidation.Price), float64(c.Targets[0].Price.Price), c.Quality.Overall, c.Reaction != nil)
}

func decisionFromCandidate(c opportunity.Candidate, barTime int64, ctx *analysiscontext.MarketContext, pip float64) *Decision {
	frame := ctx.Timeframes[market.M5].Legacy
	d := &Decision{
		Model: "D", Direction: string(c.Direction), EvalTime: barTime, TouchTime: c.Reaction.TouchBarTime, ConfirmTime: c.Reaction.ConfirmationBarTime,
		EntryLow: c.Entry.Low, EntryHigh: c.Entry.High, Stop: float64(c.Invalidation.Price), Target: float64(c.Targets[0].Price.Price),
		TargetKind: c.Targets[0].Price.Label, Pattern: c.Reaction.Pattern, ATR: frame.DetectorATR, Key: "D|" + c.ID,
	}
	if c.DetectorConfluence != nil {
		d.Stars = c.DetectorConfluence.SelectedStars
	}
	d.Independent = int(c.Quality.Components["independent_fact_count"])
	fillGeometry(d, pip)
	return d
}

// addModelA adds the legacy running-intersection decisions of the capture.
func (r *Run) addModelA(t testing.TB) {
	b := loadBaseline(t)
	cap, ok := b.Captures[r.Capture.Baseline]
	if !ok {
		return
	}
	for _, x := range cap.Decisions {
		d := &Decision{
			Model: "A", Symbol: r.Capture.Symbol, Direction: x.Direction, EvalTime: x.Bar, TouchTime: x.Touch, ConfirmTime: x.Confirm,
			EntryLow: x.EntryLow, EntryHigh: x.EntryHigh, Stop: x.Invalidation, Target: x.Target, Pattern: x.Pattern,
			Key: fmt.Sprintf("A|%s|%d|%.4f", x.Direction, x.Confirm, x.EntryLow),
		}
		if i, ok := r.Idx[x.Bar]; ok {
			lo := i + 1 - 400
			if lo < 0 {
				lo = 0
			}
			d.ATR = techniquezone.DetectorATR(r.Bars[lo:i+1], 14, 1)
		}
		fillGeometry(d, r.Pip)
		r.Decisions["A"] = append(r.Decisions["A"], d)
	}
	// A is a frozen what-if outside its production period; mark nothing here, the
	// reports split by period.
	_ = b.RegimeEnd
}

// simulateAll prices every decision and marks which ones the Algo Bot could place.
func (r *Run) simulateAll() {
	for model, ds := range r.Decisions {
		sort.SliceStable(ds, func(i, j int) bool { return ds[i].EvalTime < ds[j].EvalTime })
		for _, d := range ds {
			if model == "B" || model == "C" {
				d.FailedAtPublication = r.failedAtPublication(d)
			}
			d.Executable = r.StopMaxPips <= 0 || d.RiskPips <= r.StopMaxPips+1e-9
			d.Single = simulate(r.Bars, r.Idx, d, false, 0)
			d.Ladder = simulate(r.Bars, r.Idx, d, true, r.StopMinPips*r.Pip)
		}
		r.Decisions[model] = ds
	}
}

func (r *Run) failedAtPublication(d *Decision) bool {
	ci, ok1 := r.Idx[d.ConfirmTime]
	ei, ok2 := r.Idx[d.EvalTime]
	if !ok1 || !ok2 {
		return false
	}
	c := r.Bars[ci]
	for k := ci + 1; k <= ei; k++ {
		if d.Direction == "BUY" && r.Bars[k].Close < c.Low || d.Direction == "SELL" && r.Bars[k].Close > c.High {
			return true
		}
	}
	return false
}
