// Command analysis-engine is the future shadow-mode analysis service
// (rebuild-analysis-engine.md §26 Stage 7: "Go processes real production
// data and stores comparison telemetry only" — Python remains
// authoritative until Stage 8). Not wired to Redis/manifest yet: this is
// Stage 1/2 of the migration (domain types + pure math), not a runnable
// service. See docs/go-analysis-migration-audit.md for what's ported so
// far and what Stage 3+ still needs.
package main

import (
	"fmt"
	"os"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
)

func main() {
	fmt.Fprintln(os.Stderr, "analysis-engine: Stage 1/2 scaffold only — not yet wired to Redis or live decisions.")
	if path := os.Getenv(config.ManifestFileEnv); path != "" {
		m, err := config.Load(path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "analysis-engine: failed to load manifest: %v\n", err)
			os.Exit(1)
		}
		fmt.Fprintf(os.Stderr, "analysis-engine: loaded manifest version=%d profile=%q live_instruments=%v\n",
			m.ManifestVersion, m.Profile, m.LiveInstruments)
		return
	}
	fmt.Fprintf(os.Stderr, "analysis-engine: %s not set, nothing to load.\n", config.ManifestFileEnv)
}
