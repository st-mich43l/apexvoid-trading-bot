// Package manuallevels_test measures which structures the owner's manual entries and stops sit on, and whether the analysis engine sees them.
//
//	MANUAL_SIGNALS=signals.jsonl MANUAL_CAPTURES=captures/ MANUAL_OUT=out/ go test ./test/manuallevels -run TestManualLevelAttribution -timeout 40m
//
// MANUAL_SIGNALS is the JSONL written by algo-bot/tools/manual_level_export.py; MANUAL_CAPTURES is a directory of captures made by tools.capture_bars (the
// committed testdata/replay-*-production-capture-*.json are always merged in). Per symbol the captures are merged by bar time, the merged bars are replayed
// through the real engine, and the state as of the last closed bar before each signal is attributed. MANUAL_OUT receives detail.jsonl (one row per signal,
// production prices: local only, never committed) and results.json (aggregates only).
//
// This only describes: nothing here changes a strategy, the engine or a configuration, and nothing is tuned from it.
package manuallevels_test

import (
	"bufio"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

type signal struct {
	ID          int64    `json:"id"`
	TS          int64    `json:"ts"`
	Symbol      string   `json:"symbol"`
	Action      string   `json:"action"`
	Entry       float64  `json:"entry"`
	EntryEnd    *float64 `json:"entry_end"`
	SL          *float64 `json:"sl"`
	OriginalSL  *float64 `json:"original_sl"`
	SetupType   string   `json:"setup_type"`
	TradeStream string   `json:"trade_stream"`
	Status      string   `json:"status"`
	FillState   string   `json:"fill_state"`
	Closed      []struct {
		ExitPath string `json:"exit_path"`
	} `json:"closed_results"`
}

func loadSignals(path string) ([]signal, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	var out []signal
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 1<<20), 1<<24)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if !strings.HasPrefix(line, "{") {
			continue
		}
		var s signal
		if err := json.Unmarshal([]byte(line), &s); err != nil {
			return nil, err
		}
		s.Symbol, s.Action = strings.ToUpper(s.Symbol), strings.ToUpper(s.Action)
		out = append(out, s)
	}
	return out, scanner.Err()
}

// ---- captures -------------------------------------------------------------------------------------------------------------------------------------

// maxM5Gap is the longest hole in the merged M5 series the replay is allowed to run across: older bars before a longer hole are dropped (the engine
// would otherwise warm up across missing days). A weekend is under three days.
const maxM5Gap = 4 * 86400

// mergeCaptures combines captures of one symbol per timeframe, deduplicating by bar time (the first capture wins), and trims the M5 series after its last
// hole longer than maxM5Gap.
func mergeCaptures(caps []*replaycapture.Capture) *replaycapture.Capture {
	merged := &replaycapture.Capture{Version: 1, Description: "merged captures", Columns: []string{"t", "open", "high", "low", "close", "volume"}, Timeframes: map[string][][]float64{}}
	byTF := map[string]map[int64][]float64{}
	for _, c := range caps {
		merged.Symbol = c.Symbol
		for tf, rows := range c.Timeframes {
			if byTF[tf] == nil {
				byTF[tf] = map[int64][]float64{}
			}
			for _, row := range rows {
				if _, seen := byTF[tf][int64(row[0])]; !seen {
					byTF[tf][int64(row[0])] = row
				}
			}
		}
	}
	for tf, rows := range byTF {
		times := make([]int64, 0, len(rows))
		for t := range rows {
			times = append(times, t)
		}
		sort.Slice(times, func(i, j int) bool { return times[i] < times[j] })
		if tf == "M5" {
			start := 0
			for i := 1; i < len(times); i++ {
				if times[i]-times[i-1] > maxM5Gap {
					start = i
				}
			}
			times = times[start:]
		}
		for _, t := range times {
			merged.Timeframes[tf] = append(merged.Timeframes[tf], rows[t])
		}
	}
	return merged
}

func capturesFor(symbol string, dirs ...string) ([]*replaycapture.Capture, []string, error) {
	var files []string
	for _, dir := range dirs {
		matches, _ := filepath.Glob(filepath.Join(dir, "*.json"))
		files = append(files, matches...)
	}
	sort.Strings(files)
	var out []*replaycapture.Capture
	var used []string
	for _, file := range files {
		if strings.Contains(file, "-m1-") {
			continue
		}
		c, err := replaycapture.Load(file)
		if err != nil {
			continue // not a capture (a results file in the same directory)
		}
		if strings.EqualFold(string(c.Symbol), symbol) {
			out = append(out, c)
			used = append(used, filepath.Base(file))
		}
	}
	return out, used, nil
}

// ---- the state the engine sees -------------------------------------------------------------------------------------------------------------------

type stateKey struct {
	tf market.Timeframe
	t  int64
}

type snapshot struct {
	stateTF    market.Timeframe
	barTime    int64
	atr        float64
	atrFrom    market.Timeframe
	price      float64
	roundStep  float64
	pip        float64
	structures []structure
	counts     map[string]int
	h1Closes   []float64
	// pdhInM5Frame: the M5 detector frame (150 bars) holds a previous-day high/low; the M15 and H1 frames' windows may hold it when the M5 one does not.
	pdhInM5Frame bool
	frames       map[market.Timeframe]bool
}

type candidateRow struct {
	Strategy, Direction, StructureTF string
	Low, High, Invalidation          float64
	Stars                            int
	BarTime                          int64
}

// levelFamilies are the horizontal price levels (a zone or a line is not a level that is "crossed with acceptance").
var levelFamilies = map[string]bool{"swing_level": true, "swing_level_fractal": true, "round_number": true, "session_high_low": true, "pdh_pdl": true, "pwh_pwl": true, "liquidity_pool": true}

func sessionFamily(name string) (family, tf string) {
	upper := strings.ToUpper(name)
	switch {
	case upper == "PDH" || upper == "PDL":
		return "pdh_pdl", ""
	case upper == "PWH" || upper == "PWL":
		return "pwh_pwl", ""
	case strings.HasPrefix(upper, "ASIA"):
		return "session_high_low", "Asia"
	case strings.HasPrefix(upper, "LONDON"):
		return "session_high_low", "London"
	case strings.HasPrefix(upper, "NY"):
		return "session_high_low", "NY"
	}
	return "session_high_low", "other"
}

// collect turns one engine evaluation into the structures every family offers at that bar.
func collect(e engine.Evaluation, roundStep, pip float64) snapshot {
	snap := snapshot{counts: map[string]int{}, frames: map[market.Timeframe]bool{}, roundStep: roundStep, pip: pip, barTime: e.BarTime, stateTF: e.Timeframe}
	add := func(s structure) {
		snap.structures = append(snap.structures, s)
		snap.counts[s.key()]++
	}
	if m5 := e.Context.Timeframes[market.M5]; m5 != nil && m5.Legacy != nil && m5.Legacy.DetectorATR > 0 {
		snap.atr, snap.atrFrom = m5.Legacy.DetectorATR, market.M5
	} else if frame := e.Context.Timeframes[e.Timeframe]; frame != nil && frame.Legacy != nil {
		snap.atr, snap.atrFrom = frame.Legacy.DetectorATR, e.Timeframe
	}
	if frame := e.Context.Timeframes[e.Timeframe]; frame != nil && len(frame.Candles) > 0 {
		snap.price = frame.Candles[len(frame.Candles)-1].Close
	}
	seenSessions := map[string]bool{}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		frameContext := e.Context.Timeframes[tf]
		if frameContext == nil || frameContext.Legacy == nil || len(frameContext.Legacy.Bars) < 10 {
			continue
		}
		snap.frames[tf] = true
		frame := frameContext.Legacy
		label := string(tf)
		for _, l := range frame.Levels {
			add(structure{Family: "swing_level", TF: label, Low: l.Price - l.Band, High: l.Price + l.Band})
		}
		for _, l := range frame.SwingLevels {
			add(structure{Family: "swing_level_fractal", TF: label, Low: l.Price - l.Band, High: l.Price + l.Band})
		}
		ob, sd, fvg := techniquezone.SourceViews(frame.TechniqueZones)
		for _, z := range sd {
			add(structure{Family: "supply_demand", TF: label, Low: z.Bottom, High: z.Top})
		}
		for _, z := range ob {
			add(structure{Family: "order_block", TF: label, Low: z.Bottom, High: z.Top})
		}
		for _, z := range fvg {
			add(structure{Family: "fvg", TF: label, Low: z.Bottom, High: z.Top})
		}
		for _, p := range frame.Pools {
			add(structure{Family: "liquidity_pool", TF: label, Low: p.Level - p.Band, High: p.Level + p.Band})
		}
		for _, line := range frame.Trendlines {
			if line.BrokenAt != nil {
				continue
			}
			v := float64(trendline.ValueAt(line, len(frame.Bars)-1))
			add(structure{Family: "trendline", TF: label, Low: v, High: v})
		}
		for _, ref := range frame.Sessions {
			family, name := sessionFamily(ref.Name)
			id := fmt.Sprintf("%s|%.5f", strings.ToUpper(ref.Name), ref.Price)
			if tf == market.M5 && family == "pdh_pdl" {
				snap.pdhInM5Frame = true
			}
			if seenSessions[id] { // the same level read from another frame's window
				continue
			}
			seenSessions[id] = true
			add(structure{Family: family, TF: name, Low: ref.Price, High: ref.Price})
		}
	}
	if h1 := e.Context.Timeframes[market.H1]; h1 != nil {
		candles := h1.Candles
		if len(candles) > 48 {
			candles = candles[len(candles)-48:]
		}
		for _, c := range candles {
			snap.h1Closes = append(snap.h1Closes, c.Close)
		}
	}
	return snap
}

// roundStructures are the round-number levels within reach of the entry and the stop (every multiple of the instrument's round step).
func roundStructures(step, low, high float64) []structure {
	if step <= 0 {
		return nil
	}
	var out []structure
	for r := math.Floor(low/step) * step; r <= high+step; r += step {
		out = append(out, structure{Family: "round_number", Low: r, High: r})
	}
	return out
}

// ---- attribution ----------------------------------------------------------------------------------------------------------------------------------

type distanceRow struct {
	Pips     float64 `json:"pips"`
	ATR      float64 `json:"atr"`
	Explains bool    `json:"explains"`
}

type anchorRow struct {
	GapPips float64 `json:"gap_pips"`
	GapATR  float64 `json:"gap_atr"`
}

type attribution struct {
	ID        int64  `json:"id"`
	Symbol    string `json:"symbol"`
	Action    string `json:"action"`
	SetupType string `json:"setup_type"`
	Stream    string `json:"stream"`
	Filled    bool   `json:"filled"`

	Covered    bool     `json:"covered"`
	StateTF    string   `json:"state_tf"`
	AgeSeconds int64    `json:"age_seconds"`
	ATRFrom    string   `json:"atr_from"`
	Frames     []string `json:"frames"`
	ATRPips    float64  `json:"atr_pips"`

	EntryProx float64 `json:"entry_proximal"`
	Stop      float64 `json:"stop"`
	HasStop   bool    `json:"has_stop"`
	StopPips  float64 `json:"stop_pips"`
	StopATR   float64 `json:"stop_atr"`

	Entry     map[string]distanceRow `json:"entry"`
	Protected map[string]anchorRow   `json:"protected"`
	Beyond    map[string]anchorRow   `json:"beyond"`
	Explained []string               `json:"explained_by"`

	Primary      string   `json:"primary_structure"`
	AcceptedFlip bool     `json:"primary_crossed_with_acceptance"`
	HasPrimary   bool     `json:"has_primary"`
	HasLevel     bool     `json:"has_level"`
	LevelPrimary string   `json:"primary_level"`
	LevelCrossed bool     `json:"primary_level_crossed_with_acceptance"`
	AEMatched    bool     `json:"ae_matched"`
	AEStrategies []string `json:"ae_strategies"`
	AEKeyLevel   bool     `json:"ae_key_level"`
	AECandidates int      `json:"ae_candidates_in_window"`
	PDHInM5Frame bool     `json:"pdh_pdl_in_m5_frame"`
	AENearestATR float64  `json:"ae_nearest_atr"`
	// Null is the chance baseline: the same state with the entry moved by each of nullOffsets M5 ATR. NullExplained counts, per family, the offsets the
	// family would have explained; NullAEMatched the offsets some same-side candidate would have matched.
	NullN         int            `json:"null_n"`
	NullExplained map[string]int `json:"null_explained"`
	NullAEMatched int            `json:"null_ae_matched"`
}

// nullOffsets are the entry shifts (M5 ATR) that give every family its chance rate: far enough from the owner's entry that it is not the owner's structure,
// near enough to be a price the owner might have chosen.
var nullOffsets = []float64{-3, -2, -1.5, -1, -0.5, 0.5, 1, 1.5, 2, 3}

func (s signal) filled() bool { return s.FillState == "filled" }

// attribute computes everything for one signal from the engine state and the candidates of its window.
func attribute(s signal, snap *snapshot, window []candidateRow, ageSeconds int64) attribution {
	a := attribution{ID: s.ID, Symbol: s.Symbol, Action: s.Action, SetupType: s.SetupType, Stream: s.TradeStream, Filled: s.filled(),
		Entry: map[string]distanceRow{}, Protected: map[string]anchorRow{}, Beyond: map[string]anchorRow{}}
	a.EntryProx = proximalEntry(s.Action, s.Entry, s.EntryEnd)
	if stop, ok := stopPrice(s.OriginalSL, s.SL); ok {
		a.Stop, a.HasStop = stop, true
	}
	if snap == nil {
		return a
	}
	a.Covered, a.StateTF, a.AgeSeconds, a.ATRFrom = true, string(snap.stateTF), ageSeconds, string(snap.atrFrom)
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		if snap.frames[tf] {
			a.Frames = append(a.Frames, string(tf))
		}
	}
	atr, pip := snap.atr, snap.pip
	a.ATRPips = atr / pip
	a.PDHInM5Frame = snap.pdhInM5Frame
	list := append([]structure(nil), snap.structures...)
	lo, hi := a.EntryProx, a.EntryProx
	if a.HasStop {
		lo, hi = math.Min(lo, a.Stop), math.Max(hi, a.Stop)
	}
	list = append(list, roundStructures(snap.roundStep, lo-snap.roundStep, hi+snap.roundStep)...)
	byKey := map[string][]structure{}
	for _, st := range list {
		byKey[st.key()] = append(byKey[st.key()], st)
	}
	bestDistance, bestKey := math.Inf(1), ""
	var bestStructure structure
	levelDistance, levelKey := math.Inf(1), ""
	var levelStructure structure
	keys := make([]string, 0, len(byKey))
	for k := range byKey {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, key := range keys {
		near, d, ok := nearest(a.EntryProx, byKey[key])
		if !ok {
			continue
		}
		row := distanceRow{Pips: round(d / pip), ATR: round(d / atr), Explains: explains(d, atr)}
		a.Entry[key] = row
		if row.Explains {
			a.Explained = append(a.Explained, key)
			if d < bestDistance || d == bestDistance && key < bestKey {
				bestDistance, bestKey, bestStructure = d, key, near
			}
			if levelFamilies[strings.SplitN(key, "|", 2)[0]] && (d < levelDistance || d == levelDistance && key < levelKey) {
				levelDistance, levelKey, levelStructure = d, key, near
			}
		}
		if a.HasStop {
			if prot, gap, ok := protectedBy(a.Action, a.EntryProx, a.Stop, byKey[key]); ok {
				a.Protected[key] = anchorRow{GapPips: round(gap / pip), GapATR: round(gap / atr)}
				_ = prot
			}
			if _, gap, ok := nearestBeyond(a.Action, a.Stop, byKey[key]); ok {
				a.Beyond[key] = anchorRow{GapPips: round(gap / pip), GapATR: round(gap / atr)}
			}
		}
	}
	if a.HasStop {
		a.StopPips = round(math.Abs(a.EntryProx-a.Stop) / pip)
		a.StopATR = round(math.Abs(a.EntryProx-a.Stop) / atr)
	}
	if bestKey != "" {
		a.HasPrimary, a.Primary = true, bestKey
		a.AcceptedFlip = acceptedBeyond(a.Action, bestStructure, snap.h1Closes)
	}
	if levelKey != "" {
		a.HasLevel, a.LevelPrimary = true, levelKey
		a.LevelCrossed = acceptedBeyond(a.Action, levelStructure, snap.h1Closes)
	}
	a.AECandidates = len(window)
	a.AENearestATR = -1
	seen := map[string]bool{}
	for _, c := range window {
		if c.Direction != a.Action {
			continue
		}
		d := distance(a.EntryProx, structure{Low: c.Low, High: c.High})
		if a.AENearestATR < 0 || d/atr < a.AENearestATR {
			a.AENearestATR = round(d / atr)
		}
		if explains(d, atr) {
			a.AEMatched = true
			if !seen[c.Strategy] {
				seen[c.Strategy] = true
				a.AEStrategies = append(a.AEStrategies, c.Strategy)
			}
			if c.Strategy == "key_level" {
				a.AEKeyLevel = true
			}
		}
	}
	sort.Strings(a.AEStrategies)

	// Chance baseline.
	a.NullN, a.NullExplained = len(nullOffsets), map[string]int{}
	spread := 3*atr + snap.roundStep
	nullByKey := map[string][]structure{}
	for _, st := range append(append([]structure(nil), snap.structures...), roundStructures(snap.roundStep, a.EntryProx-spread, a.EntryProx+spread)...) {
		nullByKey[st.key()] = append(nullByKey[st.key()], st)
	}
	for _, k := range nullOffsets {
		price := a.EntryProx + k*atr
		for key, list := range nullByKey {
			if _, d, ok := nearest(price, list); ok && explains(d, atr) {
				a.NullExplained[key]++
			}
		}
		for _, c := range window {
			if c.Direction == a.Action && explains(distance(price, structure{Low: c.Low, High: c.High}), atr) {
				a.NullAEMatched++
				break
			}
		}
	}
	return a
}

// ---- the replay -----------------------------------------------------------------------------------------------------------------------------------

type target struct {
	tf  market.Timeframe
	bar int64
	age int64
}

// chooseState picks the engine state for a signal: the last closed M5 bar at or before ts (at most an hour old), else the lowest covered timeframe's
// last closed bar (M15 within four hours, H1 within twelve).
func chooseState(capture *replaycapture.Capture, ts int64) (target, bool) {
	for _, c := range []struct {
		tf      market.Timeframe
		seconds int64
		maxAge  int64
	}{{market.M5, 300, 3600}, {market.M15, 900, 4 * 3600}, {market.H1, 3600, 12 * 3600}} {
		var best int64 = -1
		for _, row := range capture.Timeframes[string(c.tf)] {
			t := int64(row[0])
			if t+c.seconds <= ts && t > best {
				best = t
			}
		}
		if best >= 0 && ts-(best+c.seconds) <= c.maxAge {
			return target{c.tf, best, ts - (best + c.seconds)}, true
		}
	}
	return target{}, false
}

func analyse(t *testing.T, doc *config.Document, symbol string, capture *replaycapture.Capture, signals []signal) []attribution {
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, symbol); err != nil {
		t.Fatal(err)
	}
	pip, roundStep := settings.Geometry.PipSize, settings.LegacyRead.RoundStep

	targets := make([]target, len(signals))
	covered := make([]bool, len(signals))
	wanted := map[stateKey][]int{}
	windows := map[int64][]int{} // M5 bar time -> signals whose +-12 bar window contains it
	for i, s := range signals {
		targets[i], covered[i] = chooseState(capture, s.TS)
		if !covered[i] {
			continue
		}
		wanted[stateKey{targets[i].tf, targets[i].bar}] = append(wanted[stateKey{targets[i].tf, targets[i].bar}], i)
		center := (s.TS - 300) / 300 * 300
		for k := int64(-11); k <= 12; k++ {
			windows[center+k*300] = append(windows[center+k*300], i)
		}
	}
	snaps := make([]*snapshot, len(signals))
	candidates := make([][]candidateRow, len(signals))
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{WithoutStopBudget: true, OnEvaluation: func(e engine.Evaluation) {
		if idx := wanted[stateKey{e.Timeframe, e.BarTime}]; len(idx) > 0 {
			snap := collect(e, roundStep, pip)
			for _, i := range idx {
				copied := snap
				snaps[i] = &copied
			}
		}
		if e.Timeframe != market.M5 {
			return
		}
		for _, i := range windows[e.BarTime] {
			for _, c := range e.Candidates {
				row := candidateRow{Strategy: string(c.Strategy), Direction: string(c.Direction), StructureTF: string(c.StructureTimeframe), Low: float64(c.Entry.Low), High: float64(c.Entry.High),
					Invalidation: float64(c.Invalidation.Price), BarTime: e.BarTime}
				if c.DetectorConfluence != nil {
					row.Stars = c.DetectorConfluence.SelectedStars
				}
				candidates[i] = append(candidates[i], row)
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	out := make([]attribution, len(signals))
	for i, s := range signals {
		out[i] = attribute(s, snaps[i], candidates[i], targets[i].age)
	}
	return out
}

// run attributes every signal of every symbol against the merged captures.
func run(t *testing.T, doc *config.Document, signals []signal, captureDirs []string) (attributions []attribution, used map[string][]string) {
	used = map[string][]string{}
	bySymbol := map[string][]signal{}
	for _, s := range signals {
		bySymbol[s.Symbol] = append(bySymbol[s.Symbol], s)
	}
	symbols := make([]string, 0, len(bySymbol))
	for sym := range bySymbol {
		symbols = append(symbols, sym)
	}
	sort.Strings(symbols)
	for _, sym := range symbols {
		caps, files, err := capturesFor(sym, captureDirs...)
		if err != nil || len(caps) == 0 {
			t.Fatalf("no captures for %s in %v", sym, captureDirs)
		}
		used[sym] = files
		attributions = append(attributions, analyse(t, doc, sym, mergeCaptures(caps), bySymbol[sym])...)
	}
	return attributions, used
}

func TestManualLevelAttribution(t *testing.T) {
	path, out := os.Getenv("MANUAL_SIGNALS"), os.Getenv("MANUAL_OUT")
	if path == "" || out == "" {
		t.Skip("MANUAL_SIGNALS and MANUAL_OUT not set")
	}
	signals, err := loadSignals(path)
	if err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	dirs := []string{filepath.Join("..", "..", "testdata")}
	if extra := os.Getenv("MANUAL_CAPTURES"); extra != "" {
		dirs = append(dirs, extra)
	}
	attributions, used := run(t, doc, signals, dirs)
	if err := os.MkdirAll(out, 0o755); err != nil {
		t.Fatal(err)
	}
	detail, err := os.Create(filepath.Join(out, "detail.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	enc := json.NewEncoder(detail)
	for _, a := range attributions {
		if err := enc.Encode(a); err != nil {
			t.Fatal(err)
		}
	}
	detail.Close()
	raw, err := json.MarshalIndent(aggregate(attributions, used), "", " ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(out, "results.json"), append(raw, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Logf("%d signals attributed", len(attributions))
	_ = fmt.Sprint
}
