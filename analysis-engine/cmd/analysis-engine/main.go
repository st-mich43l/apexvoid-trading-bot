// Command analysis-engine is the future shadow-mode analysis service
// (rebuild-analysis-engine.md §26 Stage 7: "Go processes real production
// data and stores comparison telemetry only" — Python remains
// authoritative until Stage 8). Not wired to Redis yet: this is Stage
// 1/2 of the analysis migration (domain types + pure math) plus Stage C4
// of the configuration migration (direct Configuration V3 YAML reading —
// see docs/configuration-v3-migration-audit.md), not a runnable service.
package main

import (
	"fmt"
	"os"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
)

func main() {
	fmt.Fprintln(os.Stderr, "analysis-engine: scaffold only — not yet wired to Redis or live decisions.")
	path := os.Getenv(config.RootFileEnv)
	if path == "" {
		fmt.Fprintf(os.Stderr, "analysis-engine: %s not set, nothing to load.\n", config.RootFileEnv)
		return
	}
	doc, err := config.ResolveDocument(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "analysis-engine: failed to load configuration: %v\n", err)
		os.Exit(1)
	}
	live, err := doc.LiveInstruments()
	if err != nil {
		fmt.Fprintf(os.Stderr, "analysis-engine: failed to read live instruments: %v\n", err)
		os.Exit(1)
	}
	environment, _ := doc.Get("runtime.environment")
	fmt.Fprintf(os.Stderr, "analysis-engine: loaded config_root=%s environment=%v live_instruments=%v\n",
		path, environment, live)
}
