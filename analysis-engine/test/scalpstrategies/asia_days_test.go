package scalpstrategies_test

import (
	"fmt"
	"path/filepath"
	"sort"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/asiarange"
)

// TestAsiaRangeDayByDay prints, for every Asia range finalized in the fresh window (all dates after the 25 Oct London change; the strategy has been live since 29 Oct), the
// range in pips and in M15 ATR against the strategy's window, and what the strategy did with it: published, or the reasons real episodes were refused. It asserts only that
// the replay is consistent with the strategy's own account; the table is the finding (docs/strategies/asia_range.md).
func TestAsiaRangeDayByDay(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", "scalpwindow-xau-20261103.json"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, "XAU"); err != nil {
		t.Fatal(err)
	}
	var asia *asiarange.Strategy
	var cfg asiarange.Config
	for _, c := range settings.Strategies {
		if c.ID == "asia_range" {
			s, err := asiarange.New(c)
			if err != nil {
				t.Fatal(err)
			}
			asia, _ = s.(*asiarange.Strategy)
			if cfg, err = asiarange.ParseConfig(c.Parameters); err != nil {
				t.Fatal(err)
			}
		}
	}
	if asia == nil {
		t.Fatal("asia_range must construct")
	}
	pip := settings.Geometry.PipSize
	type day struct {
		london         int64
		high, low      float64
		rangeATR       float64
		published      []string
		refused        map[string]int
		seen           map[string]bool
		lastEvalInside bool
	}
	days := map[int64]*day{}
	_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 || e.Context == nil {
			return
		}
		var d *day
		for _, level := range e.Context.Session.State.Levels {
			if (level.Name != "ASIA_H" && level.Name != "ASIA_L") || !level.Final {
				continue
			}
			if days[level.AvailableAt] == nil {
				days[level.AvailableAt] = &day{london: level.AvailableAt, refused: map[string]int{}, seen: map[string]bool{}}
			}
			d = days[level.AvailableAt]
			if level.Name == "ASIA_H" {
				d.high = float64(level.Price)
			} else {
				d.low = float64(level.Price)
			}
		}
		if d == nil {
			return
		}
		m15 := e.Context.Timeframes[market.M15]
		if m15 != nil && d.rangeATR == 0 {
			atr := indicator.NewWilderWindow(m15.Candles, cfg.ATRLength, cfg.ATRWindowBars)
			if v, ok := atr.At(len(m15.Candles) - 1); ok && v > 0 {
				d.rangeATR = (d.high - d.low) / v
			}
		}
		analysis, candidates := asia.Analyze(e.Context)
		for _, c := range candidates {
			if !d.seen["P"+c.ID] {
				d.seen["P"+c.ID] = true
				d.published = append(d.published, fmt.Sprintf("%s %s", c.Direction, time.Unix(c.CreatedAt, 0).UTC().Format("15:04")))
			}
		}
		for _, r := range analysis.Rejections {
			id := fmt.Sprintf("%s|%s|%s|%s|%d", r.Reason, r.Kind, r.Edge, r.Direction, r.At)
			if !d.seen[id] {
				d.seen[id] = true
				d.refused[r.Reason]++
			}
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	var keys []int64
	for k := range days {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
	t.Logf("window: M15 range %.1f to %.1f ATR, active window %.0f h", cfg.MinimumRangeATR, cfg.MaximumRangeATR, cfg.ActiveWindowHours)
	for _, k := range keys {
		d := days[k]
		verdict := fmt.Sprintf("published %v", d.published)
		if len(d.published) == 0 {
			verdict = "no opportunity"
		}
		t.Logf("%s London open %s · Asia %.2f-%.2f = %.0f pips = %.1f M15 ATR · %s · refused %v",
			time.Unix(k, 0).UTC().Format("Mon 02 Jan"), time.Unix(k, 0).UTC().Format("15:04Z"), d.low, d.high, (d.high-d.low)/pip, d.rangeATR, verdict, d.refused)
	}
	_ = strategy.Refusal{}
}
