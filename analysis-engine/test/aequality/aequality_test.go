// Package aequality replays every registered strategy over the committed real
// production captures and measures, per unique opportunity, what a technical
// certification needs: the geometry the strategy published (entry band, technical
// invalidation, technical target), the executable geometry the instrument's stop
// envelope allows, and what a conservative candle-causal simulation of that
// geometry would have done. It is research tooling plus invariants: the invariants
// are asserted on every run, the dump is written only when AEQ_DUMP is set.
//
//	AEQ_DUMP=/tmp/aeq go test ./test/aequality -run TestReplayAllStrategies -v
//
// then algo-bot/tools/ae_quality_eval.py aggregates it. Nothing here changes a
// strategy. Simulated outcomes are hypothetical opportunity outcomes: no broker
// behaviour, assumed constant spreads, single-leg entry at the proximal edge.
package aequality_test

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

type capture struct{ name, symbol, file string }

// Order matters: the first capture to see an opportunity owns it (its bars simulate it).
var captures = []capture{
	{"XAU Sep", "XAU", "replay-xau-production-capture-20260921.json"},
	{"XAU M1 Oct 2-6", "XAU", "replay-xau-m1-production-capture-20261006.json"},
	{"XAU Oct M5", "XAU", "replay-xau-production-capture-20261013.json"},
	{"XAU Oct 14 (M1)", "XAU", "replay-xau-production-capture-20261014.json"},
	{"EURUSD early", "EURUSD", "replay-eurusd-production-capture-20261006.json"},
	{"EURUSD", "EURUSD", "replay-eurusd-production-capture-20261013.json"},
	{"EURUSD Oct 14", "EURUSD", "replay-eurusd-production-capture-20261014.json"},
	{"GBPUSD early", "GBPUSD", "replay-gbpusd-production-capture-20261005.json"},
	{"GBPUSD", "GBPUSD", "replay-gbpusd-production-capture-20261013.json"},
	{"GBPUSD Oct 14", "GBPUSD", "replay-gbpusd-production-capture-20261014.json"},
	{"USDJPY early", "USDJPY", "replay-usdjpy-production-capture-20261005.json"},
	{"USDJPY", "USDJPY", "replay-usdjpy-production-capture-20261013.json"},
	{"USDJPY Oct 14", "USDJPY", "replay-usdjpy-production-capture-20261014.json"},
	{"GBPJPY early", "GBPJPY", "replay-gbpjpy-production-capture-20261006.json"},
	{"GBPJPY", "GBPJPY", "replay-gbpjpy-production-capture-20261013.json"},
	{"GBPJPY Oct 14", "GBPJPY", "replay-gbpjpy-production-capture-20261014.json"},
}

var pipSize = map[string]float64{"XAU": 0.1, "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "GBPJPY": 0.01}

// Assumed constant spreads (price units): the captured bars are bid-only.
var spread = map[string]float64{"XAU": 0.25, "EURUSD": 0.00008, "GBPUSD": 0.0001, "USDJPY": 0.008, "GBPJPY": 0.02}

// Scalp strategies trade a 1R / 2R ladder on every instrument; the XAU zone book uses 1R-4R.
var scalps = map[string]bool{"range_edge": true, "fade_scalp": true, "impulse_pullback": true, "scalp_breakout_retest": true}

type outcome struct {
	Result string  `json:"result"` // target | stop | stop_be | timeout | unfilled | not_executable | censored
	R      float64 `json:"r"`
}

// Record is one unique opportunity (the first closed bar that published it).
type Record struct {
	Capture      string             `json:"capture"`
	Symbol       string             `json:"symbol"`
	Strategy     string             `json:"strategy"`
	Version      string             `json:"version"`
	ID           string             `json:"id"`
	Direction    string             `json:"direction"`
	EvalTime     int64              `json:"eval_time"`
	BarTF        string             `json:"bar_tf"`
	ObservedTF   string             `json:"observed_tf"`
	StructureTF  string             `json:"structure_tf,omitempty"`
	EntryLow     float64            `json:"entry_low"`
	EntryHigh    float64            `json:"entry_high"`
	Invalidation float64            `json:"invalidation"`
	Targets      []float64          `json:"targets"`
	TargetLabels []string           `json:"target_labels"`
	Quality      float64            `json:"quality"`
	Components   map[string]float64 `json:"components"`
	Stars        int                `json:"stars"`
	V2Raw        float64            `json:"v2_raw"`
	Factors      map[string]any     `json:"factors,omitempty"`
	HasReaction  bool               `json:"has_reaction"`
	DetectorConf bool               `json:"detector_confluence"`
	Evidence     []string           `json:"evidence"`
	ATR          float64            `json:"atr"`
	RefPrice     float64            `json:"ref_price"`
	FloorPips    float64            `json:"floor_pips"`
	CapPips      float64            `json:"cap_pips"`
	CreatedAt    int64              `json:"created_at"`
	ExpiresAt    int64              `json:"expires_at"`
	FormedAt     int64              `json:"formed_at"`
	StructuralID string             `json:"structural_id"`
	Repeats      int                `json:"repeats"`
	// Geometry, in pips of the instrument.
	EntryWidthPips float64 `json:"entry_width_pips"`
	TechRiskPips   float64 `json:"tech_risk_pips"`   // proximal edge to the technical invalidation
	TechRewardPips float64 `json:"tech_reward_pips"` // proximal edge to the first technical target
	TechRR         float64 `json:"tech_rr"`
	ExecRiskPips   float64 `json:"exec_risk_pips"` // the invalidation widened to the envelope floor
	// RoomCover is the first technical target distance over the executable risk: below 1 the
	// plan's 1R target lies beyond the strategy's own technical objective.
	RoomCover  float64            `json:"room_cover"`
	Executable bool               `json:"executable"` // technical stop within the envelope cap
	Violations []string           `json:"violations,omitempty"`
	Sim        map[string]outcome `json:"sim"`
}

func repo(parts ...string) string {
	return filepath.Join(append([]string{"..", "..", ".."}, parts...)...)
}

type run struct {
	records  []*Record
	bars     map[string][]market.Candle // per capture and timeframe
	barsBy   map[string]map[int64]int
	evals    int
	firstBar int64
	lastBar  int64
}

func timeframeSeconds(tf string) int64 {
	switch tf {
	case "M1":
		return 60
	case "M5":
		return 300
	case "M15":
		return 900
	case "H1":
		return 3600
	}
	return 300
}

func replayCapture(t *testing.T, doc *config.Document, c capture) *run {
	loaded, err := replaycapture.Load(filepath.Join("..", "..", "testdata", c.file))
	if err != nil {
		t.Fatal(err)
	}
	r := &run{bars: map[string][]market.Candle{}, barsBy: map[string]map[int64]int{}}
	for _, tf := range []market.Timeframe{market.M1, market.M5} {
		if candles, err := loaded.Candles(tf); err == nil && len(candles) > 0 {
			r.bars[string(tf)] = candles
			index := map[int64]int{}
			for i, b := range candles {
				index[b.Time] = i
			}
			r.barsBy[string(tf)] = index
		}
	}
	byID := map[string]*Record{}
	_, err = replaycapture.Replay(doc, loaded, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		r.evals++
		for _, cand := range e.Candidates {
			if rec, ok := byID[cand.ID]; ok {
				rec.Repeats++
				continue
			}
			rec := record(c, e, cand)
			byID[cand.ID] = rec
			r.records = append(r.records, rec)
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func record(c capture, e engine.Evaluation, cand opportunity.Candidate) *Record {
	pip := pipSize[c.symbol]
	rec := &Record{
		Capture: c.name, Symbol: string(cand.Symbol), Strategy: string(cand.Strategy), Version: cand.StrategyVersion, ID: cand.ID,
		Direction: string(cand.Direction), EvalTime: e.BarTime + timeframeSeconds(string(e.Timeframe)), BarTF: string(e.Timeframe),
		ObservedTF: string(cand.ObservedTimeframe), StructureTF: string(cand.StructureTimeframe),
		EntryLow: float64(cand.Entry.Low), EntryHigh: float64(cand.Entry.High), Invalidation: float64(cand.Invalidation.Price),
		Quality: cand.Quality.Overall, Components: cand.Quality.Components, HasReaction: cand.Reaction != nil,
		DetectorConf: cand.DetectorConfluence != nil, CreatedAt: cand.CreatedAt, ExpiresAt: cand.ExpiresAt, FormedAt: cand.FormedAt,
		StructuralID: cand.StructuralID, Repeats: 1, Sim: map[string]outcome{},
	}
	for _, target := range cand.Targets {
		rec.Targets = append(rec.Targets, float64(target.Price.Price))
		rec.TargetLabels = append(rec.TargetLabels, target.Price.Label)
	}
	for _, ev := range cand.Evidence {
		rec.Evidence = append(rec.Evidence, ev.Code)
	}
	if cand.StopEnvelope != nil {
		rec.FloorPips, rec.CapPips = cand.StopEnvelope.FloorPips, cand.StopEnvelope.CapPips
	}
	if tech := cand.Technical; tech != nil {
		rec.ATR, rec.RefPrice = tech.ATR, tech.ReferencePrice
		if tech.Confluence != nil {
			rec.Stars, rec.V2Raw = tech.Confluence.SelectedStars, tech.Confluence.V2Raw
			f := tech.Confluence.Factors
			rec.Factors = map[string]any{
				"htf_aligned": f.HTFAligned, "touches": f.Touches, "wick": f.WickRejection, "displacement": f.DisplacementGrade,
				"session": f.SessionContext, "structural": f.StructuralAgreement, "fib": f.FibTouch, "choch": f.CHoCH,
				"zone_quality": tech.Confluence.ZoneQualityScore, "raw_factor": tech.Confluence.RawFactorScore,
			}
		}
	}
	proximal := rec.EntryHigh
	if rec.Direction == "SELL" {
		proximal = rec.EntryLow
	}
	rec.EntryWidthPips = (rec.EntryHigh - rec.EntryLow) / pip
	rec.TechRiskPips = math.Abs(proximal-rec.Invalidation) / pip
	if len(rec.Targets) > 0 {
		rec.TechRewardPips = math.Abs(rec.Targets[0]-proximal) / pip
	}
	if rec.TechRiskPips > 0 {
		rec.TechRR = rec.TechRewardPips / rec.TechRiskPips
	}
	rec.ExecRiskPips = math.Max(rec.TechRiskPips, rec.FloorPips)
	rec.Executable = rec.CapPips <= 0 || rec.TechRiskPips <= rec.CapPips+1e-9
	if rec.ExecRiskPips > 0 {
		rec.RoomCover = rec.TechRewardPips / rec.ExecRiskPips
	}
	rec.Violations = invariants(rec)
	return rec
}

// invariants are the geometry rules every published opportunity must satisfy.
func invariants(r *Record) []string {
	var v []string
	finite := func(x float64) bool { return !math.IsNaN(x) && !math.IsInf(x, 0) }
	if r.Direction != "BUY" && r.Direction != "SELL" {
		v = append(v, "direction")
	}
	if !(finite(r.EntryLow) && finite(r.EntryHigh) && r.EntryLow > 0 && r.EntryHigh >= r.EntryLow) {
		v = append(v, "entry_band")
	}
	if r.Direction == "BUY" && !(r.Invalidation < r.EntryLow) || r.Direction == "SELL" && !(r.Invalidation > r.EntryHigh) {
		v = append(v, "stop_not_beyond_entry_band")
	}
	if len(r.Targets) == 0 {
		v = append(v, "no_target")
	} else if r.Direction == "BUY" && !(r.Targets[0] > r.EntryHigh) || r.Direction == "SELL" && !(r.Targets[0] < r.EntryLow) {
		v = append(v, "target_not_beyond_entry_band")
	}
	if !(finite(r.Quality) && r.Quality >= 0 && r.Quality <= 1) {
		v = append(v, "quality_range")
	}
	// Rebuilt strategies publish measurements (ATR multiples, counts) as components, so a component
	// only has to be finite; the score the ranking reads (Overall) must be in [0, 1].
	for _, component := range r.Components {
		if !finite(component) {
			v = append(v, "component_not_finite")
			break
		}
	}
	if r.CreatedAt > r.ExpiresAt {
		v = append(v, "created_after_expiry")
	}
	if r.FormedAt > r.CreatedAt+1 {
		v = append(v, "formed_after_created")
	}
	if r.CreatedAt > r.EvalTime {
		v = append(v, "created_after_the_closed_bar")
	}
	if !(r.ATR > 0 && finite(r.ATR)) {
		v = append(v, "atr_not_positive")
	}
	if r.FloorPips <= 0 || r.CapPips < r.FloorPips {
		v = append(v, "stop_envelope")
	}
	return v
}

// simulate places the order when the evaluated bar closes and fills it only on a LATER bar that
// trades to the proximal edge (ask for a buy, bid for a sell). The fill bar can only stop the trade
// out; a stop and a target in one bar count as the stop; after the first target the stop moves to
// break-even. The risk is the technical invalidation widened to the envelope floor; beyond the cap
// the trade is not executable. Held up to 24 hours or the end of the data.
func simulate(r *Record, bars []market.Candle, index map[int64]int, barSeconds int64, ladder string, cost float64) outcome {
	if r.Direction != "BUY" && r.Direction != "SELL" {
		return outcome{Result: "not_executable"}
	}
	pip := pipSize[r.Symbol]
	sell := r.Direction == "SELL"
	var mult, ratio []float64
	switch ladder {
	case "oneR":
		mult, ratio = []float64{1}, []float64{1}
	case "twoR":
		mult, ratio = []float64{2}, []float64{1}
	default:
		if r.Symbol == "XAU" && !scalps[r.Strategy] {
			mult, ratio = []float64{1, 2, 3, 4}, []float64{.4, .2, .2, .2}
		} else {
			mult, ratio = []float64{1, 2}, []float64{.5, .5}
		}
	}
	e, ok := index[r.EvalTime-barSeconds]
	if !ok {
		return outcome{Result: "censored"}
	}
	px := r.EntryHigh
	if sell {
		px = r.EntryLow
	}
	fillIdx, fill := -1, 0.0
	limit := r.EvalTime + 12*3600
	if r.ExpiresAt > 0 && r.ExpiresAt < limit {
		limit = r.ExpiresAt
	}
	for j := e + 1; j < len(bars) && bars[j].Time < limit; j++ {
		b := bars[j]
		if !sell && b.Low+cost <= px {
			fillIdx, fill = j, math.Min(b.Open+cost, px)
			break
		}
		if sell && b.High >= px {
			fillIdx, fill = j, math.Max(b.Open, px)
			break
		}
	}
	if fillIdx < 0 {
		if e+1 >= len(bars) || bars[len(bars)-1].Time < limit-barSeconds {
			return outcome{Result: "censored"}
		}
		return outcome{Result: "unfilled"}
	}
	risk := fill - r.Invalidation
	if sell {
		risk = r.Invalidation - fill
	}
	if !(risk > 0) {
		return outcome{Result: "unfilled"}
	}
	if floor := r.FloorPips * pip; risk < floor {
		risk = floor
	}
	if r.CapPips > 0 && risk > r.CapPips*pip+1e-12 {
		return outcome{Result: "not_executable"}
	}
	dir, stop := 1.0, fill-risk
	if sell {
		dir, stop = -1, fill+risk
	}
	stopHit := func(b market.Candle, s float64) bool {
		if sell {
			return b.High+cost >= s
		}
		return b.Low <= s
	}
	reached := func(b market.Candle, price float64) bool {
		if sell {
			return b.Low+cost <= price
		}
		return b.High >= price
	}
	if stopHit(bars[fillIdx], stop) {
		return outcome{Result: "stop", R: -1}
	}
	end := fillIdx + 24*3600/int(barSeconds)
	if end > len(bars)-1 {
		end = len(bars) - 1
	}
	rem, total, cur, next, be := 1.0, 0.0, stop, 0, false
	for k := fillIdx + 1; k <= end; k++ {
		b := bars[k]
		if stopHit(b, cur) {
			total += rem * (dir * (cur - fill) / risk)
			if be {
				return outcome{Result: "stop_be", R: total}
			}
			return outcome{Result: "stop", R: total}
		}
		for next < len(mult) && reached(b, fill+dir*mult[next]*risk) {
			total += ratio[next] * mult[next]
			rem -= ratio[next]
			if next == 0 && len(mult) > 1 {
				cur, be = fill, true
			}
			next++
		}
		if next == len(mult) {
			return outcome{Result: "target", R: total}
		}
	}
	if end == len(bars)-1 && bars[end].Time < bars[fillIdx].Time+24*3600-barSeconds {
		return outcome{Result: "censored"} // the data ended while the trade was still open
	}
	return outcome{Result: "timeout", R: total + rem*dir*(bars[end].Close-fill)/risk}
}

var replayOnce sync.Once
var allRecords []*Record
var replayErr error

func replayAll(t *testing.T) []*Record {
	replayOnce.Do(func() {
		doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
		if err != nil {
			replayErr = err
			return
		}
		runs := make([]*run, len(captures))
		var wg sync.WaitGroup
		for i := range captures {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				defer func() {
					if p := recover(); p != nil {
						replayErr = fmt.Errorf("%s: %v", captures[i].name, p)
					}
				}()
				runs[i] = replayCapture(t, doc, captures[i])
			}(i)
		}
		wg.Wait()
		seen := map[string]bool{}
		for i, r := range runs {
			if r == nil {
				continue
			}
			for _, rec := range r.records {
				if seen[rec.ID] {
					continue
				}
				seen[rec.ID] = true
				tf, seconds := "M5", int64(300)
				if rec.ObservedTF == "M1" && r.bars["M1"] != nil {
					tf, seconds = "M1", 60
				}
				// A candle-by-candle simulation needs the bar the opportunity was evaluated on.
				bars, index := r.bars[tf], r.barsBy[tf]
				barSeconds := seconds
				rec.Sim["ladder"] = simulate(rec, bars, index, barSeconds, "ladder", 0)
				rec.Sim["ladder_cost"] = simulate(rec, bars, index, barSeconds, "ladder", spread[rec.Symbol])
				rec.Sim["oneR_cost"] = simulate(rec, bars, index, barSeconds, "oneR", spread[rec.Symbol])
				rec.Sim["twoR_cost"] = simulate(rec, bars, index, barSeconds, "twoR", spread[rec.Symbol])
				rec.Capture = captures[i].name
				allRecords = append(allRecords, rec)
			}
		}
		sort.SliceStable(allRecords, func(i, j int) bool {
			if allRecords[i].EvalTime != allRecords[j].EvalTime {
				return allRecords[i].EvalTime < allRecords[j].EvalTime
			}
			return allRecords[i].ID < allRecords[j].ID
		})
	})
	if replayErr != nil {
		t.Fatal(replayErr)
	}
	return allRecords
}

// TestReplayAllStrategies asserts the invariants over every unique opportunity of every
// strategy and writes the dump when AEQ_DUMP is set.
func TestReplayAllStrategies(t *testing.T) {
	records := replayAll(t)
	if len(records) == 0 {
		t.Fatal("no opportunity replayed")
	}
	violations := map[string]map[string]int{}
	counts := map[string]int{}
	for _, r := range records {
		counts[r.Strategy]++
		for _, v := range r.Violations {
			if violations[r.Strategy] == nil {
				violations[r.Strategy] = map[string]int{}
			}
			violations[r.Strategy][v]++
		}
	}
	var names []string
	for s := range counts {
		names = append(names, s)
	}
	sort.Strings(names)
	for _, s := range names {
		t.Logf("%-22s unique=%5d violations=%v", s, counts[s], violations[s])
		if len(violations[s]) > 0 {
			t.Errorf("%s published opportunities that break the geometry invariants: %v", s, violations[s])
		}
	}
	if dir := os.Getenv("AEQ_DUMP"); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		out, err := os.Create(filepath.Join(dir, "records.jsonl"))
		if err != nil {
			t.Fatal(err)
		}
		defer out.Close()
		enc := json.NewEncoder(out)
		for _, r := range records {
			if err := enc.Encode(r); err != nil {
				t.Fatal(err)
			}
		}
	}
}

// TestTrendlineTargetIsAnOpposingLiquidityAtLeastOneATR pins Trendline v4: its first target is the nearest
// opposing liquidity at least minimum_target_distance_atr (1.0) from the entry, never a projection. v3 targeted
// twice the distance to the interaction band edge, a median 0.05 of the executable stop.
func TestTrendlineTargetIsAnOpposingLiquidityAtLeastOneATR(t *testing.T) {
	published := 0
	for _, r := range replayAll(t) {
		if r.Strategy != "trendline" {
			continue
		}
		published++
		if len(r.TargetLabels) == 0 || r.TargetLabels[0] != "nearest_opposing_liquidity" {
			t.Errorf("%s %s: target labels %v, want nearest_opposing_liquidity", r.Symbol, r.ID, r.TargetLabels)
		}
		if r.TechRewardPips*pipSize[r.Symbol] < r.ATR-1e-9 {
			t.Errorf("%s %s: technical reward %.1f pips is under one ATR (%.4f)", r.Symbol, r.ID, r.TechRewardPips, r.ATR)
		}
	}
	t.Logf("trendline v4 setups in the captures: %d", published)
}

// TestSupplyDemandZonesAreNotSpent pins Supply and Demand v3: a confirmed reaction on a zone already touched
// more than maximum_zone_touches (2) times does not publish. Zones touched three or more times averaged
// -0.06R/-0.21R (supply) and -0.31R/-0.39R (demand) in the dev and out-of-sample halves.
func TestSupplyDemandZonesAreNotSpent(t *testing.T) {
	confirmed := 0
	for _, r := range replayAll(t) {
		if (r.Strategy != "supply" && r.Strategy != "demand") || !r.HasReaction {
			continue
		}
		confirmed++
		touches, ok := r.Factors["touches"].(int)
		if !ok {
			t.Fatalf("%s %s: no touches factor", r.Strategy, r.ID)
		}
		if touches > 2 {
			t.Errorf("%s %s %s: published on a zone touched %d times", r.Symbol, r.Strategy, r.ID, touches)
		}
	}
	if confirmed == 0 {
		t.Fatal("no confirmed supply or demand setup in the captures")
	}
	t.Logf("confirmed supply/demand setups: %d", confirmed)
}
