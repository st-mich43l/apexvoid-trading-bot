// Package rereplay replays Range Edge (v2 and v3) against real production
// captures, over the same closed candles, with causal hypothetical fills.
// Nothing here is production code.
package rereplay_test

import (
	"path/filepath"
	"runtime"
	"testing"
	"time"

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

func timeUTC(ts int64) time.Time { return time.Unix(ts, 0).UTC() }

// The v3 rejection reasons, as the strategy reports them.
const (
	ReasonEdgeBreak        = "edge_accepted_break"
	ReasonNotReestablished = "range_not_reestablished"
)
