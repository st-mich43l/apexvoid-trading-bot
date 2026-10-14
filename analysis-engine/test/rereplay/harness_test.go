package rereplay_test

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"sort"
	"sync"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/rangeedge"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// Capture is one committed real production capture (closed M5/M15/H1 candles).
type Capture struct {
	Name, Symbol, File string
}

var captures = []Capture{
	{"USDJPY", "USDJPY", "replay-usdjpy-production-capture-20261013.json"},
	{"EURUSD", "EURUSD", "replay-eurusd-production-capture-20261013.json"},
	{"GBPUSD", "GBPUSD", "replay-gbpusd-production-capture-20261013.json"},
	{"GBPJPY", "GBPJPY", "replay-gbpjpy-production-capture-20261013.json"},
	{"XAU Sep", "XAU", "replay-xau-production-capture-20260921.json"},
	{"XAU Oct", "XAU", "replay-xau-production-capture-20261013.json"},
}

// Decision is one Range Edge opportunity as the engine published it, first seen at EvalTime.
type Decision struct {
	Version     string  `json:"version"`
	Capture     string  `json:"capture"`
	Symbol      string  `json:"symbol"`
	ID          string  `json:"id"`
	Direction   string  `json:"direction"`
	EvalTime    int64   `json:"eval_time"`
	TouchTime   int64   `json:"touch_time"`
	ConfirmTime int64   `json:"confirm_time"`
	EntryLow    float64 `json:"entry_low"`
	EntryHigh   float64 `json:"entry_high"`
	Invalidate  float64 `json:"invalidation"`
	Equilibrium float64 `json:"equilibrium"`
	Opposite    float64 `json:"opposite_edge"`
	Pattern     string  `json:"pattern"`
	Stars       int     `json:"stars"`
	ATR         float64 `json:"atr"`
	Structure   string  `json:"structure"`
}

// Rejection is an edge the v3 integrity assessment refused (first seen at EvalTime).
type Rejection struct {
	Capture   string `json:"capture"`
	Direction string `json:"direction"`
	Reason    string `json:"reason"`
	EvalTime  int64  `json:"eval_time"`
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
	Decisions   []*Decision
	Rejections  []Rejection
	// OthersSHA is the hash of every other strategy's candidates, to prove they are untouched.
	Others []string
}

func decisionFrom(c opportunity.Candidate, evalTime int64, version, capture, symbol string) *Decision {
	d := &Decision{
		Version: version, Capture: capture, Symbol: symbol, ID: c.ID, Direction: string(c.Direction), EvalTime: evalTime,
		EntryLow: float64(c.Entry.Low), EntryHigh: float64(c.Entry.High), Invalidate: float64(c.Invalidation.Price),
		ATR: 0, Structure: c.StructuralID,
	}
	if c.Reaction != nil {
		d.TouchTime, d.ConfirmTime, d.Pattern = c.Reaction.TouchBarTime, c.Reaction.ConfirmationBarTime, c.Reaction.Pattern
	}
	if len(c.Targets) > 0 {
		d.Equilibrium = float64(c.Targets[0].Price.Price)
	}
	if len(c.Targets) > 1 {
		d.Opposite = float64(c.Targets[1].Price.Price)
	}
	if c.DetectorConfluence != nil {
		d.Stars = c.DetectorConfluence.SelectedStars
	}
	return d
}

var (
	runsMu sync.Mutex
	runs   = map[string]*Run{}
)

func replay(t testing.TB, c Capture) *Run {
	runsMu.Lock()
	defer runsMu.Unlock()
	if r, ok := runs[c.Name]; ok {
		return r
	}
	r := doReplay(t, c, loadCapture(t, c.File), nil)
	runs[c.Name] = r
	return r
}

// doReplay replays one (possibly altered) capture through the production engine path.
func doReplay(t testing.TB, c Capture, capture *replaycapture.Capture, analyze func(*analysiscontext.MarketContext) rangeedge.Analysis) *Run {
	doc := loadDoc(t)
	settings := symbolSettings(t, doc, c.Symbol)
	params := strategyParams(t, settings, "range_edge")
	legacy, err := strategyutil.ParseLegacyDetectorSettings(params)
	if err != nil {
		t.Fatal(err)
	}
	m5, err := capture.Candles(market.M5)
	if err != nil {
		t.Fatal(err)
	}
	r := &Run{Capture: c, Bars: m5, Idx: map[int64]int{}, Pip: legacy.PipSize, StopMinPips: settings.InstrumentStopMinPips, StopMaxPips: settings.InstrumentStopMaxPips}
	for i, b := range m5 {
		r.Idx[b.Time] = i
	}
	version := rangeedge.Version
	if analyze == nil {
		strat, err := rangeedge.New(strategy.Config{ID: rangeedge.ID, Version: rangeedge.Version, Enabled: true, Parameters: params})
		if err != nil {
			t.Fatal(err)
		}
		analyze = strat.(interface {
			Analyze(*analysiscontext.MarketContext) rangeedge.Analysis
		}).Analyze
	}
	seen, rejSeen := map[string]bool{}, map[string]bool{}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		r.Evaluations++
		a := analyze(e.Context)
		for _, rej := range a.Rejections {
			bucket := rej.RetiredBreakTime
			if bucket == 0 {
				bucket = e.BarTime / 3600 * 3600 // no break to name: one per hour
			}
			key := fmt.Sprintf("%s|%s|%d", rej.Direction, rej.Reason, bucket)
			if !rejSeen[key] {
				rejSeen[key] = true
				r.Rejections = append(r.Rejections, Rejection{Capture: c.Name, Direction: string(rej.Direction), Reason: rej.Reason, EvalTime: e.BarTime})
			}
		}
		for _, cand := range e.Candidates {
			if string(cand.Strategy) != "range_edge" {
				r.Others = append(r.Others, fmt.Sprintf("%d|%s|%s|%s|%.6f|%.6f|%.6f", e.BarTime, cand.Strategy, cand.ID, cand.Direction, float64(cand.Entry.Low), float64(cand.Entry.High), float64(cand.Invalidation.Price)))
				continue
			}
			if cand.Reaction == nil || seen[cand.ID] {
				continue
			}
			seen[cand.ID] = true
			d := decisionFrom(cand, e.BarTime, version, c.Name, c.Symbol)
			if base, ok := strategyutil.NewLegacyDetectorForFrame(e.Context, market.M5, legacy); ok {
				d.ATR = base.ATR
			}
			r.Decisions = append(r.Decisions, d)
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(r.Others)
	return r
}

// readBaseline reads the frozen v2 decisions committed under testdata.
func readBaseline(t testing.TB) map[string][]*Decision {
	t.Helper()
	raw, err := os.ReadFile(testdata("range-edge-v2-baseline.json"))
	if err != nil {
		t.Fatal(err)
	}
	var out map[string][]*Decision
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func round(v float64, places int) float64 {
	p := math.Pow(10, float64(places))
	return math.Round(v*p) / p
}
