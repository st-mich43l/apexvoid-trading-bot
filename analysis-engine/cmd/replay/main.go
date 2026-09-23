// Command replay feeds closed bars chronologically through the exact same
// internal/engine.Engine.Dispatch path a live feed uses — source task
// §47: "no special replay-only structure implementation... only the
// event source differs." It exists to (a) exercise the pipeline against
// real historical data (testdata/raw_xau_m5_snapshot.jsonl is real
// production XAU M5 data, not synthetic) and (b) optionally render the
// final read via internal/visualization.
//
// Usage:
//
//	go run ./cmd/replay -bars testdata/raw_xau_m5_snapshot.jsonl \
//	  -config ../config/apexvoid.yml -symbol XAU -timeframe M5 \
//	  -png /tmp/replay.png
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/visualization"
)

// jsonBar mirrors testdata/raw_xau_m5_snapshot.jsonl's real field shape —
// the same short keys (t/o/h/l/c/v) the Redis bars:{SYMBOL}:{TF} snapshot
// dumps this repo already uses elsewhere (see the Go migration audit's
// own fixture tooling).
type jsonBar struct {
	T int64   `json:"t"`
	O float64 `json:"o"`
	H float64 `json:"h"`
	L float64 `json:"l"`
	C float64 `json:"c"`
	V float64 `json:"v"`
}

func main() {
	barsPath := flag.String("bars", "", "path to a JSONL file of bars ({\"t\":...,\"o\":...,\"h\":...,\"l\":...,\"c\":...,\"v\":...} per line), oldest first")
	configPath := flag.String("config", "", "path to config/apexvoid.yml (Configuration V3 root)")
	symbol := flag.String("symbol", "XAU", "symbol these bars belong to")
	timeframe := flag.String("timeframe", "M5", "timeframe these bars belong to")
	pngPath := flag.String("png", "", "optional: write a PNG of the final structure/liquidity read here")
	setupDir := flag.String("setup-png-dir", "", "optional: directory for setup-focused PNGs, one per selected discovered opportunity")
	strategyID := flag.String("strategy", "", "optional: only render discovered opportunities from this strategy ID")
	opportunityID := flag.String("opportunity-id", "", "optional: exact ID or stable ID prefix of one discovered opportunity to render")
	beforeBars := flag.Int("before-bars", 100, "candles to show before a setup in each setup-focused PNG")
	afterBars := flag.Int("after-bars", 30, "candles to show after a setup in each setup-focused PNG")
	maxSetups := flag.Int("max-setups", 0, "maximum selected opportunities to render (0 = all)")
	verbose := flag.Bool("verbose", false, "print every final live opportunity as well as aggregate counts")
	flag.Parse()

	if *barsPath == "" || *configPath == "" {
		fmt.Fprintln(os.Stderr, "usage: replay -bars <file.jsonl> -config <apexvoid.yml> [-symbol XAU] [-timeframe M5] [-png out.png] [-setup-png-dir dir -strategy id|-opportunity-id id-prefix]")
		os.Exit(2)
	}

	if err := run(*barsPath, *configPath, market.Symbol(*symbol), market.Timeframe(*timeframe), *pngPath, *setupDir, *strategyID, *opportunityID, *beforeBars, *afterBars, *maxSetups, *verbose); err != nil {
		fmt.Fprintln(os.Stderr, "replay:", err)
		os.Exit(1)
	}
}

func run(barsPath, configPath string, symbol market.Symbol, tf market.Timeframe, pngPath, setupDir, strategyID, opportunityID string, beforeBars, afterBars, maxSetups int, verbose bool) error {
	if beforeBars < 0 || afterBars < 0 || maxSetups < 0 {
		return fmt.Errorf("before-bars, after-bars, and max-setups must be non-negative")
	}
	doc, err := config.ResolveDocument(configPath)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	settings, err := engine.LoadSettings(doc, tf, false)
	if err != nil {
		return fmt.Errorf("loading Analysis Engine V2 settings: %w", err)
	}

	candles, err := readBars(barsPath)
	if err != nil {
		return fmt.Errorf("reading bars: %w", err)
	}
	if len(candles) == 0 {
		return fmt.Errorf("no bars in %s", barsPath)
	}

	e := engine.NewEngine(nil)
	if err := e.Register(symbol, settings); err != nil {
		return fmt.Errorf("registering %s: %w", symbol, err)
	}

	var snap engine.AnalysisSnapshot
	discovered := make([]capturedOpportunity, 0)
	seen := make(map[string]struct{})
	accepted := 0
	for index, c := range candles {
		result, err := e.Dispatch(marketdata.BarEvent{Symbol: symbol, Timeframe: tf, Candle: c})
		if err != nil {
			return fmt.Errorf("dispatching bar at t=%d: %w", c.Time, err)
		}
		snap = result
		for _, candidate := range snap.Opportunities {
			if _, exists := seen[candidate.ID]; exists {
				continue
			}
			seen[candidate.ID] = struct{}{}
			discovered = append(discovered, capturedOpportunity{Candidate: candidate, Snapshot: snap, Index: index})
		}
		accepted++
	}

	structState := snap.Structure[tf]
	liqState := snap.Liquidity[tf]
	fmt.Printf("replayed %d bars for %s %s\n", accepted, symbol, tf)
	fmt.Printf("  swings: %d (micro trend=%v internal=%v intermediate=%v major=%v)\n",
		len(structState.Swings), snap.Context.Timeframes[tf].Structure.Micro.Trend,
		snap.Context.Timeframes[tf].Structure.Internal.Trend,
		snap.Context.Timeframes[tf].Structure.Intermediate.Trend,
		snap.Context.Timeframes[tf].Structure.Major.Trend)
	fmt.Printf("  breaks: %d\n", len(structState.Breaks))
	fmt.Printf("  liquidity pools: %d\n", len(liqState.Pools))
	fmt.Printf("  bias: %v (layer=%v)\n", snap.Context.Bias.Direction, snap.Context.Bias.Layer)
	fmt.Printf("  version: structure=%s liquidity=%s zone=%s\n", snap.Version.StructureVersion, snap.Version.LiquidityVersion, snap.Version.ZoneVersion)
	fmt.Printf("  discovered opportunities: %d\n", len(discovered))
	printStrategyCounts("discovered by strategy", discovered)
	fmt.Printf("  live opportunities (Phase S8): %d\n", len(snap.Opportunities))
	if verbose {
		for _, opp := range snap.Opportunities {
			fmt.Printf("    - %s %s %s entry=[%.5f,%.5f] invalidation=%.5f quality=%.2f\n",
				opp.Strategy, opp.Direction, shortID(opp.ID), opp.Entry.Low, opp.Entry.High,
				float64(opp.Invalidation.Price), opp.Quality.Overall)
		}
	}

	if pngPath != "" {
		f, err := os.Create(pngPath)
		if err != nil {
			return fmt.Errorf("creating %s: %w", pngPath, err)
		}
		defer f.Close()
		if err := visualization.RenderSnapshot(candles, snap, tf, nil, visualization.DefaultOptions(), f); err != nil {
			return fmt.Errorf("rendering PNG: %w", err)
		}
		fmt.Printf("  wrote %s\n", pngPath)
	}
	if setupDir != "" {
		selected, err := selectOpportunities(discovered, strategyID, opportunityID, maxSetups)
		if err != nil {
			return err
		}
		if err := os.MkdirAll(setupDir, 0o755); err != nil {
			return fmt.Errorf("creating setup PNG directory: %w", err)
		}
		for _, captured := range selected {
			start, end := replayWindow(captured.Index, beforeBars, afterBars, len(candles))
			name := fmt.Sprintf("%s-%s-%d-%s.png", symbol, captured.Candidate.Strategy, captured.Candidate.CreatedAt, shortID(captured.Candidate.ID))
			path := filepath.Join(setupDir, name)
			f, err := os.Create(path)
			if err != nil {
				return fmt.Errorf("creating setup PNG %s: %w", path, err)
			}
			err = visualization.RenderSnapshot(candles[start:end], captured.Snapshot, tf, &captured.Candidate, visualization.DefaultOptions(), f)
			closeErr := f.Close()
			if err != nil {
				return fmt.Errorf("rendering setup PNG %s: %w", path, err)
			}
			if closeErr != nil {
				return fmt.Errorf("closing setup PNG %s: %w", path, closeErr)
			}
			fmt.Printf("  wrote setup %s (%s %s at %d, candles=%d:%d)\n", path, captured.Candidate.Strategy, captured.Candidate.ID, captured.Candidate.CreatedAt, start, end)
		}
	}
	return nil
}

// capturedOpportunity keeps the immutable snapshot from the first observed
// lifecycle appearance of an opportunity. Setup images may include later
// candles for review, but their zones, structure, and candidate geometry stay
// exactly as they were when the technical setup was established.
type capturedOpportunity struct {
	Candidate opportunity.Candidate
	Snapshot  engine.AnalysisSnapshot
	Index     int
}

func printStrategyCounts(label string, discovered []capturedOpportunity) {
	counts := make(map[opportunity.StrategyID]int)
	for _, captured := range discovered {
		counts[captured.Candidate.Strategy]++
	}
	strategyIDs := make([]string, 0, len(counts))
	for strategyID := range counts {
		strategyIDs = append(strategyIDs, string(strategyID))
	}
	sort.Strings(strategyIDs)

	parts := make([]string, 0, len(strategyIDs))
	for _, strategyID := range strategyIDs {
		parts = append(parts, fmt.Sprintf("%s=%d", strategyID, counts[opportunity.StrategyID(strategyID)]))
	}
	fmt.Printf("  %s: %s\n", label, strings.Join(parts, ", "))
}

func selectOpportunities(discovered []capturedOpportunity, strategyID, opportunityID string, maximum int) ([]capturedOpportunity, error) {
	if strategyID != "" && opportunityID != "" {
		return nil, fmt.Errorf("strategy and opportunity-id cannot be used together")
	}
	selected := make([]capturedOpportunity, 0)
	for _, captured := range discovered {
		if strategyID != "" && string(captured.Candidate.Strategy) != strategyID {
			continue
		}
		if opportunityID != "" && !strings.HasPrefix(captured.Candidate.ID, opportunityID) {
			continue
		}
		selected = append(selected, captured)
		if maximum > 0 && len(selected) == maximum {
			break
		}
	}
	if len(selected) == 0 {
		return nil, fmt.Errorf("no discovered opportunity matched strategy=%q opportunity-id=%q", strategyID, opportunityID)
	}
	return selected, nil
}

func replayWindow(index, before, after, total int) (int, int) {
	start := index - before
	if start < 0 {
		start = 0
	}
	end := index + after + 1
	if end > total {
		end = total
	}
	return start, end
}

func shortID(id string) string {
	if len(id) <= 16 {
		return id
	}
	return id[:16]
}

func readBars(path string) ([]market.Candle, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var out []market.Candle
	scanner := bufio.NewScanner(f)
	buf := make([]byte, 0, 64*1024)
	scanner.Buffer(buf, 1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var b jsonBar
		if err := json.Unmarshal(line, &b); err != nil {
			return nil, fmt.Errorf("parsing bar: %w", err)
		}
		out = append(out, market.Candle{Time: b.T, Open: b.O, High: b.H, Low: b.L, Close: b.C, Volume: b.V})
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return out, nil
}
