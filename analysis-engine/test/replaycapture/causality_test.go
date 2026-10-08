package replaycapture_test

import (
	"path/filepath"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// TestNoStrategyUsesFutureBars is a prefix-invariance (look-ahead) check across every
// strategy, the M15 supply/demand extension and the restored detectors included.
// Cutting a capture at a time must not change what the engine decided before it: each
// candidate the full replay produced on a bar at or before the cut has to be produced,
// identically, by a replay that never saw anything later. A strategy that reads a bar
// that was not yet closed (an M15 zone anchored to the M5 bar it was still forming in, a
// frame computed over a window that includes the future) differs between the two.
func TestNoStrategyUsesFutureBars(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	full, err := replaycapture.Load(filepath.Join("..", "..", "testdata", "replay-xau-m1-production-capture-20261006.json"))
	if err != nil {
		t.Fatal(err)
	}
	decisions := func(capture *replaycapture.Capture, until int64) map[string]struct{} {
		out := map[string]struct{}{}
		_, err := replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
			if e.BarTime > until {
				return
			}
			for _, c := range e.Candidates {
				key := string(c.Strategy) + "|" + c.ID + "|" + string(e.Timeframe) + "|" + itoa(e.BarTime)
				if c.StructureTimeframe == market.M15 {
					key += "|m15"
				}
				out[key] = struct{}{}
			}
		}})
		if err != nil {
			t.Fatal(err)
		}
		return out
	}
	m5 := full.Timeframes["M5"]
	cut := int64(m5[len(m5)*6/10][0])
	closes := map[string]int64{"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
	truncated := *full
	truncated.Timeframes = map[string][][]float64{}
	for name, rows := range full.Timeframes {
		span := closes[name]
		for _, row := range rows {
			if int64(row[0])+span <= cut+300 {
				truncated.Timeframes[name] = append(truncated.Timeframes[name], row)
			}
		}
	}
	want, got := decisions(full, cut), decisions(&truncated, cut)
	if len(want) < 100 {
		t.Fatalf("the full replay produced only %d candidates up to the cut", len(want))
	}
	var missing, extra []string
	for key := range want {
		if _, ok := got[key]; !ok {
			missing = append(missing, key)
		}
	}
	for key := range got {
		if _, ok := want[key]; !ok {
			extra = append(extra, key)
		}
	}
	sort.Strings(missing)
	sort.Strings(extra)
	report := func(label string, keys []string) {
		for i, key := range keys {
			if i == 12 {
				t.Errorf("%s: ... and %d more", label, len(keys)-12)
				break
			}
			t.Errorf("%s: %s", label, key)
		}
	}
	report("only with future bars", missing)
	report("only without future bars", extra)
	m15 := 0
	for key := range want {
		if len(key) > 4 && key[len(key)-4:] == "|m15" {
			m15++
		}
	}
	if m15 == 0 {
		t.Error("the compared candidates hold no M15-structure setup, so the extension is not covered")
	}
	t.Logf("%d candidates up to the cut compared (%d on M15 structure); %d differ", len(want), m15, len(missing)+len(extra))
}

func itoa(v int64) string {
	if v == 0 {
		return "0"
	}
	neg := v < 0
	if neg {
		v = -v
	}
	var digits [20]byte
	i := len(digits)
	for v > 0 {
		i--
		digits[i] = byte('0' + v%10)
		v /= 10
	}
	if neg {
		i--
		digits[i] = '-'
	}
	return string(digits[i:])
}
