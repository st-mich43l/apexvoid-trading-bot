// Package obdefinition_test holds the evidence for the order block definition (docs/strategies/order-block-definition.md, order_block v6): fixtures cut from the
// real production candles of 28-29 Oct 2026, a book-level test of the published-opportunity retirement, parity proofs, and a measurement over every committed
// M5 capture plus the production windows (OB_DEFINITION_OUT).
package obdefinition_test

import (
	"crypto/sha256"
	"encoding/hex"
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// variant is one setting of the order block definition: the close-break buffer in ATRs (0 = rule off) and the touch limit (0 = off). A variant is applied on
// top of WithoutOBDefinition, so {0, 0} is the frozen behaviour (order_block v5) and shippedVariant is whatever config/analysis.yml says.
type variant struct {
	Buffer  float64 `json:"confirmed_break_buffer_atr"`
	Touches int     `json:"maximum_zone_touches"`
}

func (v variant) name() string {
	touches := "off"
	if v.Touches > 0 {
		touches = fmt.Sprint(v.Touches)
	}
	return fmt.Sprintf("buffer %.2f / touches %s", v.Buffer, touches)
}

// sighting is one confirmed order block candidate on one evaluated M5 bar.
type sighting struct {
	Symbol       string
	Bar          int64
	ID           string
	StructuralID string
	Direction    market.Direction
	Timeframe    market.Timeframe // the timeframe the block was found on
	Low, High    float64          // the whole structure
	FormedAt     int64
	EntryLow     float64
	EntryHigh    float64
	Zone         float64 // quality component zone_touches
	Confirmation string
	EntryDistATR float64 // how far the reference price was from the entry band, in ATR (0 inside it)
	Invalidation float64
	Withdrawn    bool
	WithdrawnAt  int64
}

type withdrawal struct {
	ID, StructuralID string
	Timeframe        market.Timeframe
	At               int64
}

type replayResult struct {
	Capture   string
	Symbol    string
	Sightings map[string]sighting // by bar|id
	First     map[string]sighting // first sight by id
	Withdrawn []withdrawal
	OthersSHA string
	Others    int
	Data      *replaycapture.Capture
}

func loadDoc(t testing.TB) *config.Document {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	return doc
}

func barLabel(unix int64) string { return time.Unix(unix, 0).UTC().Format("02 15:04") }

// shipped is the order_block configuration the repository ships (strategies.order_block).
func shipped(t testing.TB, doc *config.Document) variant {
	t.Helper()
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	for _, cfg := range settings.Strategies {
		if cfg.ID != "order_block" {
			continue
		}
		buffer, _ := cfg.Parameters["confirmed_break_buffer_atr"].(float64)
		var touches int
		switch n := cfg.Parameters["maximum_zone_touches"].(type) { // YAML integers load as int, a number written with a point as float64
		case int:
			touches = n
		case float64:
			touches = int(n)
		}
		return variant{buffer, touches}
	}
	t.Fatal("order_block is not configured")
	return variant{}
}

// replayOB replays one capture. useShipped replays the engine exactly as configured; otherwise the order block definition is removed and v is applied.
func replayOB(t testing.TB, doc *config.Document, path string, useShipped bool, v variant) *replayResult {
	t.Helper()
	capture, err := replaycapture.Load(path)
	if err != nil {
		t.Fatal(err)
	}
	out := &replayResult{Capture: filepath.Base(path), Symbol: string(capture.Symbol), Sightings: map[string]sighting{}, First: map[string]sighting{}, Data: capture}
	var others []string
	options := replaycapture.Options{
		OnEvaluation: func(e engine.Evaluation) {
			for _, c := range e.Candidates {
				if string(c.Strategy) != "order_block" {
					others = append(others, fmt.Sprintf("%s|%d|%s|%s|%.6f-%.6f|%.6f", c.Strategy, e.BarTime, c.ID, c.Direction, c.Entry.Low, c.Entry.High, float64(c.Invalidation.Price)))
					continue
				}
				if c.Reaction == nil {
					continue
				}
				s := sighting{Symbol: string(capture.Symbol), Bar: e.BarTime, ID: c.ID, StructuralID: c.StructuralID, Direction: c.Direction, EntryLow: c.Entry.Low, EntryHigh: c.Entry.High,
					Zone: c.Quality.Components["zone_touches"], Invalidation: float64(c.Invalidation.Price)}
				if c.Structure != nil {
					s.Timeframe, s.Low, s.High, s.FormedAt = c.Structure.Timeframe, c.Structure.Low, c.Structure.High, c.Structure.FormedAt
				}
				if c.Technical != nil && c.Technical.Confirmation != nil {
					s.Confirmation = c.Technical.Confirmation.Pattern
				}
				if c.Technical != nil && c.Technical.ATR > 0 {
					ref := c.Technical.ReferencePrice
					d := 0.0
					if ref < c.Entry.Low {
						d = c.Entry.Low - ref
					} else if ref > c.Entry.High {
						d = ref - c.Entry.High
					}
					s.EntryDistATR = d / c.Technical.ATR
				}
				out.Sightings[fmt.Sprintf("%d|%s", e.BarTime, c.ID)] = s
				if _, seen := out.First[c.ID]; !seen {
					out.First[c.ID] = s
				}
			}
		},
		OnWithdrawn: func(c opportunity.Candidate, at int64) {
			w := withdrawal{ID: c.ID, StructuralID: c.StructuralID, At: at}
			if c.Structure != nil {
				w.Timeframe = c.Structure.Timeframe
			}
			out.Withdrawn = append(out.Withdrawn, w)
		},
	}
	if !useShipped {
		options.WithoutOBDefinition = true
		params := map[string]any{}
		if v.Buffer > 0 {
			params["confirmed_break_buffer_atr"] = v.Buffer
		}
		if v.Touches > 0 {
			params["maximum_zone_touches"] = float64(v.Touches)
		}
		if len(params) > 0 {
			options.StrategyParameters = map[string]map[string]any{"order_block": params}
		}
	}
	if _, err := replaycapture.Replay(doc, capture, market.M5, options); err != nil {
		t.Fatal(err)
	}
	sort.Strings(others)
	sum := sha256.Sum256([]byte(strings.Join(others, "\n")))
	out.Others, out.OthersSHA = len(others), hex.EncodeToString(sum[:])
	return out
}

// brokenAt re-derives, independently of the engine, whether any close of the block's own frame after its candle, up to and including the evaluated bar, was
// beyond the far edge by more than buffer x ATR at that close (the simple rolling ATR the frames use). It returns the open time of the first such close.
func brokenAt(capture *replaycapture.Capture, s sighting, buffer float64) (int64, bool) {
	if buffer <= 0 || s.Timeframe == "" {
		return 0, false
	}
	bars, err := capture.Candles(s.Timeframe)
	if err != nil {
		return 0, false
	}
	atr := techniquezone.ATRSeries(bars, 14)
	origin := sort.Search(len(bars), func(i int) bool { return bars[i].Time >= s.FormedAt })
	if origin >= len(bars) || bars[origin].Time != s.FormedAt {
		return 0, false
	}
	// The evaluated bar is an M5 bar; a higher-frame block has only the frame's bars closed by then.
	minutes, _ := s.Timeframe.Minutes()
	for i := origin + 1; i < len(bars); i++ {
		if bars[i].Time+int64(minutes)*60 > s.Bar+300 {
			break
		}
		margin := buffer * atr[i]
		if s.Direction == market.Buy && bars[i].Close < s.Low-margin-1e-9 {
			return bars[i].Time, true
		}
		if s.Direction == market.Sell && bars[i].Close > s.High+margin+1e-9 {
			return bars[i].Time, true
		}
	}
	return 0, false
}

func near(a, b float64) bool { return math.Abs(a-b) < 0.0051 }

func captureNames(t testing.TB) []string {
	t.Helper()
	var names []string
	matches, _ := filepath.Glob(filepath.Join("..", "..", "testdata", "replay-*-production-capture-*.json"))
	for _, m := range matches {
		if !strings.Contains(m, "-m1-") {
			names = append(names, m)
		}
	}
	windows, _ := filepath.Glob(filepath.Join("..", "..", "testdata", "obwindow-*.json"))
	names = append(names, windows...)
	sort.Strings(names)
	return names
}
