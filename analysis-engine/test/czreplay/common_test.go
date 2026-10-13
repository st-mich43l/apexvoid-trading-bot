// Package czreplay certifies Confluence Zone v3 against real production captures:
// the frozen baselines it replaces (Model A, the legacy running-intersection
// detector; Model B, the Python-parity union detector), the structure-only
// variant (Model C) and the full v3 contract (Model D), over the same closed
// candles, with causal hypothetical fills. Nothing here is production code.
package czreplay_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

func repoFile(parts ...string) string {
	_, file, _, _ := runtime.Caller(0)
	root := filepath.Join(filepath.Dir(file), "..", "..", "..")
	return filepath.Join(append([]string{root}, parts...)...)
}

func testdata(name string) string { return repoFile("analysis-engine", "testdata", name) }

func loadDoc(t testing.TB) *config.Document {
	t.Helper()
	doc, err := config.ResolveDocument(repoFile("config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

// symbolSettings is the engine's own configuration for one instrument.
func symbolSettings(t testing.TB, doc *config.Document, symbol string) engine.Settings {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, symbol); err != nil {
		t.Fatal(err)
	}
	return settings
}

func strategyParams(t testing.TB, settings engine.Settings, id string) map[string]any {
	t.Helper()
	for _, s := range settings.Strategies {
		if string(s.ID) == id {
			return s.Parameters
		}
	}
	t.Fatalf("%s is not configured", id)
	return nil
}

func loadCapture(t testing.TB, name string) *replaycapture.Capture {
	t.Helper()
	c, err := replaycapture.Load(testdata(name))
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// truncate is the capture with every timeframe cut after the given time (an
// open time; the later bars are dropped), never touching the head: warm-up
// history is kept exactly as production had it.
func truncate(c *replaycapture.Capture, after int64) *replaycapture.Capture {
	out := *c
	out.Timeframes = map[string][][]float64{}
	for tf, rows := range c.Timeframes {
		var keep [][]float64
		for _, r := range rows {
			if int64(r[0]) <= after {
				keep = append(keep, r)
			}
		}
		out.Timeframes[tf] = keep
	}
	return &out
}

type tradeLedgerEntry struct {
	Symbol        string    `json:"symbol"`
	Direction     string    `json:"direction"`
	Regime        string    `json:"detector_regime"`
	Fingerprint   string    `json:"config_fingerprint"`
	AECreatedAt   string    `json:"ae_created_at"`
	AETouch       string    `json:"ae_touch"`
	AEConfirm     string    `json:"ae_confirm"`
	Pattern       string    `json:"ae_pattern"`
	Entry         []float64 `json:"ae_entry"`
	ATR           float64   `json:"ae_atr"`
	Invalidation  float64   `json:"ae_invalidation"`
	Target        float64   `json:"ae_target"`
	Stars         int       `json:"ae_stars"`
	PlanEntryType string    `json:"plan_entry_type"`
	PlanStop      string    `json:"plan_stop"`
	FillTime      string    `json:"fill_time"`
	FillPrice     float64   `json:"fill_price"`
	CloseTime     string    `json:"close_time"`
	ExitPath      string    `json:"exit_path"`
	R             float64   `json:"derived_R"`
	Pips          float64   `json:"derived_pips"`
	USD           float64   `json:"derived_usd"`
	ResultPips    float64   `json:"result_pips_col"`
	Commit        string    `json:"deployed_commit_at_fill"`
	Constituents  []struct {
		Technique string  `json:"technique"`
		Low       float64 `json:"low"`
		High      float64 `json:"high"`
		Origin    int64   `json:"origin_time"`
		Touches   int     `json:"touches"`
	} `json:"constituents"`
}

func loadLedger(t testing.TB) []tradeLedgerEntry {
	t.Helper()
	raw, err := os.ReadFile(testdata("confluence-zone-production-trades-20261013.json"))
	if err != nil {
		t.Fatal(err)
	}
	var out []tradeLedgerEntry
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}
