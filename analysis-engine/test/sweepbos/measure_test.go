// Package sweepbos_test measures fvg v9 and order_block v9 against the v8 that ran on master (test/sweepbos/fvgv8, test/sweepbos/obv8, vendored): on the SAME
// engine contexts at every closed M5 bar of the committed captures, what the prior-sweep gate refuses and what the causal breaks move. The counts are
// PUBLISHED setups, not profit; the simulated outcomes are test/aequality's (docs/strategies/liquidity-sweep-and-structure.md).
//
//	SWEEPBOS_REPORT=<file> go test ./test/sweepbos -run TestV8ToV9 -v
package sweepbos_test

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/fvg"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/orderblock"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/sweepbos/fvgv8"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/sweepbos/obv8"
)

func testdata(name string) string { return filepath.Join("..", "..", "testdata", name) }

func captures(t testing.TB) []string {
	t.Helper()
	var out []string
	for _, pattern := range []string{"replay-*-production-capture-*.json", "obwindow-*.json", "fvgwindow-*.json"} {
		matches, _ := filepath.Glob(testdata(pattern))
		for _, m := range matches {
			if !strings.Contains(m, "-m1-") {
				out = append(out, m)
			}
		}
	}
	sort.Strings(out)
	return out
}

// setup identifies a published setup across versions: the structure and the rejection bar (the opportunity id carries the version).
func setup(c opportunity.Candidate) string {
	return fmt.Sprintf("%s|%d", c.StructuralID, c.Reaction.ConfirmationBarTime)
}

// blockKey identifies a block of a structure frame by its side, its own candle's time and its structure timeframe.
type blockKey struct {
	tf     market.Timeframe
	side   string
	origin int64
}

type blockFact struct {
	confirmed int64
	kind      string
}

type result struct {
	symbol market.Symbol
	// published setups per strategy variant
	fvg8, fvgOn, fvgOff, ob8, ob9 map[string]opportunity.Candidate
	fvgRefusals, obRefusals       map[string]int64 // "tf/reason"
	fvg8Refusals                  map[string]int64
	blocks8, blocks9              map[blockKey]blockFact
	bars                          int
}

func strategyConfig(t testing.TB, settings engine.Settings, id string) strategy.Config {
	t.Helper()
	for _, c := range settings.Strategies {
		if string(c.ID) == id {
			return c
		}
	}
	t.Fatalf("strategy %s is not configured", id)
	return strategy.Config{}
}

func withParams(c strategy.Config, version string, override map[string]any) strategy.Config {
	params := map[string]any{}
	for k, v := range c.Parameters {
		params[k] = v
	}
	for k, v := range override {
		params[k] = v
	}
	c.Version, c.Parameters = version, params
	return c
}

func measure(t testing.TB, doc *config.Document, path string) *result {
	t.Helper()
	capture, err := replaycapture.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Fatal(err)
	}
	fvgCfg, obCfg := strategyConfig(t, settings, "fvg"), strategyConfig(t, settings, "order_block")
	build := func(s strategy.Strategy, err error) strategy.Strategy {
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	fvgOn := build(fvg.New(withParams(fvgCfg, fvgCfg.Version, nil))).(*fvg.Strategy)
	fvgOff := build(fvg.New(withParams(fvgCfg, fvgCfg.Version, map[string]any{"require_prior_sweep": false}))).(*fvg.Strategy)
	v8fvg := build(fvgv8.New(withParams(fvgCfg, "v8", nil))).(*fvgv8.Strategy)
	ob9 := build(orderblock.New(withParams(obCfg, obCfg.Version, nil))).(*orderblock.Strategy)
	v8ob := build(obv8.New(withParams(obCfg, "v8", nil))).(*obv8.Strategy)
	fractalN := settings.LegacyRead.FractalN

	r := &result{
		symbol: capture.Symbol,
		fvg8:   map[string]opportunity.Candidate{}, fvgOn: map[string]opportunity.Candidate{}, fvgOff: map[string]opportunity.Candidate{},
		ob8: map[string]opportunity.Candidate{}, ob9: map[string]opportunity.Candidate{},
		fvgRefusals: map[string]int64{}, obRefusals: map[string]int64{}, fvg8Refusals: map[string]int64{},
		blocks8: map[blockKey]blockFact{}, blocks9: map[blockKey]blockFact{},
	}
	record := func(into map[string]opportunity.Candidate, from []opportunity.Candidate) {
		for _, c := range from {
			if c.Reaction != nil {
				into[setup(c)] = c
			}
		}
	}
	count := func(into map[string]int64, from map[strategy.Refusal]int64) {
		for k, n := range from {
			into[string(k.Timeframe)+"/"+k.Reason] += n
		}
	}
	kindAt := func(breaks []techniquezone.Break, side string, index int) string {
		want := "up"
		if side == "sell" {
			want = "down"
		}
		for _, b := range breaks {
			if b.Index == index && b.Direction == want {
				return b.Kind
			}
		}
		return ""
	}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		r.bars++
		record(r.fvgOn, fvgOn.Evaluate(e.Context))
		count(r.fvgRefusals, fvgOn.TakeRefusals())
		record(r.fvgOff, fvgOff.Evaluate(e.Context))
		fvgOff.TakeRefusals()
		record(r.fvg8, v8fvg.Evaluate(e.Context))
		count(r.fvg8Refusals, v8fvg.TakeRefusals())
		record(r.ob9, ob9.Evaluate(e.Context))
		count(r.obRefusals, ob9.TakeRefusals())
		record(r.ob8, v8ob.Evaluate(e.Context))
		v8ob.TakeRefusals()
		// The blocks of each structure frame, with the shared breaks (v8) and with the causal ones (what v9 defines a block with).
		for _, tf := range []market.Timeframe{market.M15, market.H1} {
			tc := e.Context.Timeframes[tf]
			if tc == nil || tc.Legacy == nil {
				continue
			}
			frame := tc.Legacy
			causal := *frame
			causal.Breaks = techniquezone.StructureBreaks(frame.Swings, frame.Bars, true, fractalN)
			for _, b := range v8ob.Blocks(frame, tf) {
				r.blocks8[blockKey{tf, b.Side, frame.Bars[b.FrameOrigin].Time}] = blockFact{frame.Bars[b.FrameConfirmed].Time, kindAt(frame.Breaks, b.Side, b.FrameConfirmed)}
			}
			for _, b := range v8ob.Blocks(&causal, tf) {
				r.blocks9[blockKey{tf, b.Side, frame.Bars[b.FrameOrigin].Time}] = blockFact{frame.Bars[b.FrameConfirmed].Time, kindAt(causal.Breaks, b.Side, b.FrameConfirmed)}
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func TestV8ToV9(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	paths := captures(t)
	results := make([]*result, len(paths))
	var wg sync.WaitGroup
	gate := make(chan struct{}, runtime.NumCPU())
	for i, path := range paths {
		wg.Add(1)
		go func(i int, path string) {
			defer wg.Done()
			gate <- struct{}{}
			defer func() { <-gate }()
			results[i] = measure(t, doc, path)
		}(i, path)
	}
	wg.Wait()

	var out strings.Builder
	p := func(format string, args ...any) { fmt.Fprintf(&out, format+"\n", args...) }

	// Invariants of the change.
	for i, r := range results {
		name := filepath.Base(paths[i])
		// With the gate off v9 decides exactly as v8 did: the sweep and the break are only evidence and measures.
		for s := range r.fvgOff {
			if _, ok := r.fvg8[s]; !ok {
				t.Errorf("%s: fvg v9 without the gate published %s, which v8 did not", name, s)
			}
		}
		for s := range r.fvg8 {
			if _, ok := r.fvgOff[s]; !ok {
				t.Errorf("%s: fvg v8 published %s, which v9 without the gate did not", name, s)
			}
		}
		// With the gate on every published gap followed a sweep.
		for s, c := range r.fvgOn {
			if c.Quality.Components["prior_sweep"] != 1 {
				t.Errorf("%s: fvg v9 published %s without a prior sweep", name, s)
			}
		}
		for s, c := range r.fvgOff {
			if c.Quality.Components["prior_sweep"] == 1 && !hasSweepEvidence(c, "m5_fvg_prior_sweep_") {
				t.Errorf("%s: %s has a sweep measure and no sweep evidence", name, s)
			}
		}
	}

	type bySymbolTF struct {
		symbol market.Symbol
		tf     market.Timeframe
	}
	type cell struct{ v8, on, off, ob8, ob9 int }
	cells := map[bySymbolTF]*cell{}
	touch := func(sym market.Symbol, tf market.Timeframe) *cell {
		k := bySymbolTF{sym, tf}
		if cells[k] == nil {
			cells[k] = &cell{}
		}
		return cells[k]
	}
	fvgRefusals, obRefusals := map[string]int64{}, map[string]int64{}
	var ob8Only, ob9Only, obBoth int
	for _, r := range results {
		for _, c := range r.fvg8 {
			touch(r.symbol, c.StructureTimeframe).v8++
		}
		for _, c := range r.fvgOn {
			touch(r.symbol, c.StructureTimeframe).on++
		}
		for _, c := range r.fvgOff {
			touch(r.symbol, c.StructureTimeframe).off++
		}
		for _, c := range r.ob8 {
			touch(r.symbol, c.StructureTimeframe).ob8++
		}
		for s, c := range r.ob9 {
			touch(r.symbol, c.StructureTimeframe).ob9++
			if _, ok := r.ob8[s]; ok {
				obBoth++
			} else {
				ob9Only++
			}
		}
		for s := range r.ob8 {
			if _, ok := r.ob9[s]; !ok {
				ob8Only++
			}
		}
		for k, n := range r.fvgRefusals {
			fvgRefusals[k] += n
		}
		for k, n := range r.obRefusals {
			obRefusals[k] += n
		}
	}
	keys := make([]bySymbolTF, 0, len(cells))
	for k := range cells {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].symbol != keys[j].symbol {
			return keys[i].symbol < keys[j].symbol
		}
		return keys[i].tf < keys[j].tf
	})
	p("published setups (unique, over %d captures)\n", len(paths))
	p("| symbol | structure timeframe | fvg v8 | fvg v9 | fvg v9 gate off | order_block v8 | order_block v9 |\n|---|---|---:|---:|---:|---:|---:|")
	var total cell
	for _, k := range keys {
		c := cells[k]
		p("| %s | %s | %d | %d | %d | %d | %d |", k.symbol, k.tf, c.v8, c.on, c.off, c.ob8, c.ob9)
		total.v8, total.on, total.off, total.ob8, total.ob9 = total.v8+c.v8, total.on+c.on, total.off+c.off, total.ob8+c.ob8, total.ob9+c.ob9
	}
	p("| **total** | | %d | %d | %d | %d | %d |\n", total.v8, total.on, total.off, total.ob8, total.ob9)
	p("order_block setups: in both %d, v8 only %d, v9 only %d\n", obBoth, ob8Only, ob9Only)

	table := func(title string, counts map[string]int64) {
		names := make([]string, 0, len(counts))
		for k := range counts {
			names = append(names, k)
		}
		sort.Strings(names)
		p("%s (counted once per evaluated M5 bar a structure stays refused)\n\n| structure timeframe / reason | count |\n|---|---:|", title)
		for _, k := range names {
			p("| %s | %d |", k, counts[k])
		}
		p("")
	}
	table("fvg v9 refusals", fvgRefusals)
	table("order_block v9 refusals", obRefusals)

	// Blocks that moved because the breaks are causal.
	var same, later, earlier, vanished, appeared, kindChanged int
	blocks8, blocks9 := map[string]blockFact{}, map[string]blockFact{}
	for i, r := range results {
		for k, v := range r.blocks8 {
			blocks8[fmt.Sprintf("%d|%s|%s|%d", i, k.tf, k.side, k.origin)] = v
		}
		for k, v := range r.blocks9 {
			blocks9[fmt.Sprintf("%d|%s|%s|%d", i, k.tf, k.side, k.origin)] = v
		}
	}
	for k, a := range blocks8 {
		b, ok := blocks9[k]
		switch {
		case !ok:
			vanished++
		case b.confirmed > a.confirmed:
			later++
		case b.confirmed < a.confirmed:
			earlier++
		default:
			same++
		}
		if ok && b.confirmed == a.confirmed && a.kind != "" && b.kind != "" && a.kind != b.kind {
			kindChanged++
		}
	}
	for k := range blocks9 {
		if _, ok := blocks8[k]; !ok {
			appeared++
		}
	}
	p("order blocks of the structure frames (M15, H1), unique over the replays: v8 %d, v9 %d\n", len(blocks8), len(blocks9))
	p("| | blocks |\n|---|---:|\n| identical formation | %d |\n| formed later (the break was knowable later) | %d |\n| formed earlier | %d |\n| no longer a block | %d |\n| newly a block | %d |\n| same formation, other kind (BOS/CHoCH) | %d |", same, later, earlier, vanished, appeared, kindChanged)
	if total.on == 0 || total.ob9 == 0 {
		t.Fatal("v9 published nothing on any capture: the measurement proves nothing")
	}
	t.Log("\n" + out.String())
	if path := os.Getenv("SWEEPBOS_REPORT"); path != "" {
		if err := os.WriteFile(path, []byte(out.String()), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func hasSweepEvidence(c opportunity.Candidate, prefix string) bool {
	for _, e := range c.Evidence {
		if strings.HasPrefix(e.Code, prefix) {
			return true
		}
	}
	return false
}
