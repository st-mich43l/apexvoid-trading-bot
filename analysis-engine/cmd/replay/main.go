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

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
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
	flag.Parse()

	if *barsPath == "" || *configPath == "" {
		fmt.Fprintln(os.Stderr, "usage: replay -bars <file.jsonl> -config <apexvoid.yml> [-symbol XAU] [-timeframe M5] [-png out.png]")
		os.Exit(2)
	}

	if err := run(*barsPath, *configPath, market.Symbol(*symbol), market.Timeframe(*timeframe), *pngPath); err != nil {
		fmt.Fprintln(os.Stderr, "replay:", err)
		os.Exit(1)
	}
}

func run(barsPath, configPath string, symbol market.Symbol, tf market.Timeframe, pngPath string) error {
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
	accepted, skipped := 0, 0
	for _, c := range candles {
		result, err := e.Dispatch(marketdata.BarEvent{Symbol: symbol, Timeframe: tf, Candle: c})
		if err != nil {
			return fmt.Errorf("dispatching bar at t=%d: %w", c.Time, err)
		}
		snap = result
		accepted++
		_ = skipped
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
	fmt.Printf("  version: structure=%s liquidity=%s\n", snap.Version.StructureVersion, snap.Version.LiquidityVersion)
	fmt.Printf("  live opportunities (Phase S8): %d\n", len(snap.Opportunities))
	for _, opp := range snap.Opportunities {
		fmt.Printf("    - %s %s %s entry=[%.5f,%.5f] invalidation=%.5f quality=%.2f\n",
			opp.Strategy, opp.Direction, opp.ID[:16], opp.Entry.Low, opp.Entry.High,
			float64(opp.Invalidation.Price), opp.Quality.Overall)
	}

	if pngPath != "" {
		f, err := os.Create(pngPath)
		if err != nil {
			return fmt.Errorf("creating %s: %w", pngPath, err)
		}
		defer f.Close()
		if err := visualization.Render(candles, structState, liqState, visualization.DefaultOptions(), f); err != nil {
			return fmt.Errorf("rendering PNG: %w", err)
		}
		fmt.Printf("  wrote %s\n", pngPath)
	}
	return nil
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
