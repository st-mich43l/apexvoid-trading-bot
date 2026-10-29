// Package fvgdefinition_test holds the evidence for the fair value gap definition (docs/strategies/fvg-definition.md, fvg v6): a regression over real XAU
// setups of the committed capture, and a measurement over every committed M5 capture (FVG_DEFINITION_OUT).
package fvgdefinition_test

import (
	"fmt"
	"math"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// sighting is one confirmed FVG candidate on one evaluated M5 bar.
type sighting struct {
	Bar                               int64
	ID, StructuralID                  string
	Direction                         market.Direction
	Timeframe                         market.Timeframe // the timeframe the gap was found on
	GapLow, GapHigh                   float64
	FormedAt                          int64
	EntryLow, EntryHigh, Invalidation float64
	Target                            float64
}

type key struct {
	bar int64
	id  string
}

func loadDoc(t testing.TB) *config.Document {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

// replayFVG replays one capture and returns the confirmed FVG candidates by (evaluated bar, opportunity id). definitionOff is the v5 behaviour (the fvg
// definition keys removed).
func replayFVG(t testing.TB, doc *config.Document, name string, definitionOff bool) (map[key]sighting, *replaycapture.Capture) {
	t.Helper()
	out, _, capture := replayFVGAndOthers(t, doc, name, definitionOff)
	return out, capture
}

// replayFVGAndOthers is replayFVG that also returns every other strategy's candidate (strategy, evaluated bar, id) with its geometry, so a test can show the
// definition changes nothing else.
func replayFVGAndOthers(t testing.TB, doc *config.Document, name string, definitionOff bool) (map[key]sighting, map[string]string, *replaycapture.Capture) {
	t.Helper()
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	out := map[key]sighting{}
	others := map[string]string{}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{WithoutFVGDefinition: definitionOff, OnEvaluation: func(e engine.Evaluation) {
		for _, c := range e.Candidates {
			zoneFamily := map[string]bool{"fvg": true, "supply": true, "demand": true, "order_block": true}[string(c.Strategy)]
			if confirmed := c.Reaction != nil; zoneFamily && confirmed && c.Structure == nil {
				t.Errorf("%s %s: a confirmed %s candidate must carry its structure block", name, c.ID, c.Strategy)
			} else if !zoneFamily && c.Structure != nil {
				t.Errorf("%s %s: %s is not a zone-family strategy and must not carry a structure block", name, c.ID, c.Strategy)
			}
			if string(c.Strategy) != "fvg" {
				others[string(c.Strategy)+"|"+barLabel(e.BarTime)+"|"+c.ID] = fmt.Sprintf("%v %.6f-%.6f %.6f", c.Direction, float64(c.Entry.Low), float64(c.Entry.High), float64(c.Invalidation.Price))
				continue
			}
			if c.Reaction == nil {
				continue
			}
			if c.Structure == nil {
				t.Errorf("%s %s: a confirmed fvg candidate must carry its structure block", name, c.ID)
				continue
			}
			out[key{e.BarTime, c.ID}] = sighting{
				Bar: e.BarTime, ID: c.ID, StructuralID: c.StructuralID, Direction: c.Direction, Timeframe: c.Structure.Timeframe,
				GapLow: c.Structure.Low, GapHigh: c.Structure.High, FormedAt: c.Structure.FormedAt,
				EntryLow: float64(c.Entry.Low), EntryHigh: float64(c.Entry.High), Invalidation: float64(c.Invalidation.Price), Target: float64(c.Targets[0].Price.Price),
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	return out, others, capture
}

// thresholds are the three definition keys as configured (strategies.fvg).
type thresholds struct{ gapATR, bodyATR, bodyRatio float64 }

func configuredThresholds(t testing.TB, doc *config.Document) thresholds {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	for _, cfg := range settings.Strategies {
		if cfg.ID != "fvg" {
			continue
		}
		get := func(name string) float64 {
			v, ok := cfg.Parameters[name].(float64)
			if !ok || v <= 0 {
				t.Fatalf("strategies.fvg.%s must be configured above 0, got %v", name, cfg.Parameters[name])
			}
			return v
		}
		return thresholds{get("confirmed_minimum_gap_atr"), get("confirmed_minimum_displacement_body_atr"), get("confirmed_minimum_displacement_body_ratio")}
	}
	t.Fatal("fvg is not configured")
	return thresholds{}
}

// measured is a gap judged on the bars of the timeframe it was found on, in that timeframe's own ATR as of the candle that completed it.
type measured struct {
	GapATR, BodyATR, BodyRatio float64
	WithGap                    bool // the middle candle closed in the gap's direction
}

// measureGap restates the definition independently of the engine: the gap is the candidate's structure block, the middle candle is the bar before
// the one that completed it, the ATR is the simple rolling mean the frames use, as of the completing candle.
func measureGap(t testing.TB, capture *replaycapture.Capture, atrLength int, s sighting) (measured, bool) {
	t.Helper()
	bars, err := capture.Candles(s.Timeframe)
	if err != nil {
		t.Fatal(err)
	}
	i := sort.Search(len(bars), func(i int) bool { return bars[i].Time >= s.FormedAt })
	if i < 2 || i >= len(bars) || bars[i].Time != s.FormedAt {
		return measured{}, false
	}
	atr := techniquezone.ATRSeries(bars, atrLength)[i]
	mid := bars[i-1]
	body, span := math.Abs(mid.Close-mid.Open), mid.High-mid.Low
	m := measured{GapATR: (s.GapHigh - s.GapLow) / atr, BodyATR: body / atr, WithGap: (s.Direction == market.Buy) == (mid.Close > mid.Open)}
	if span > 0 {
		m.BodyRatio = body / span
	}
	return m, atr > 0
}

// reason is what the definition refuses a measured gap for, in the engine's order; "" when it is a real gap.
func (m measured) reason(th thresholds) string {
	const tolerance = 1e-9
	switch {
	case m.GapATR < th.gapATR-tolerance:
		return techniquezone.FVGRefusedGapSmall
	case m.BodyATR < th.bodyATR-tolerance || m.BodyRatio < th.bodyRatio-tolerance:
		return techniquezone.FVGRefusedDisplacementWeak
	case !m.WithGap:
		return techniquezone.FVGRefusedDisplacementDirection
	}
	return ""
}

func atrLengthOf(t testing.TB, doc *config.Document) int {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	return settings.LegacyRead.ATRLength
}

func captureNames(t testing.TB) []string {
	t.Helper()
	var names []string
	matches, _ := filepath.Glob(filepath.Join("..", "..", "testdata", "replay-*-production-capture-*.json"))
	for _, m := range matches {
		if !strings.Contains(m, "-m1-") {
			names = append(names, filepath.Base(m))
		}
	}
	sort.Strings(names)
	return names
}

func barLabel(unix int64) string { return time.Unix(unix, 0).UTC().Format("2006-01-02T15:04") }
