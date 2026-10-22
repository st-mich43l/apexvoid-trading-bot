package replaycapture_test

import (
	"encoding/json"
	"path/filepath"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
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

// TestOpposingBarrierBookUsesNoFutureBars is the same prefix-invariance proof for what execution reads next to the opportunity:
// the opposing-barrier book (bands, tiers, scores, touches and the zones each is built from) the publisher would write at a bar.
// A zone whose state, touch count or invalidation was decided with a bar after the cut would differ between the two replays.
func TestOpposingBarrierBookUsesNoFutureBars(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	full, err := replaycapture.Load(filepath.Join("..", "..", "testdata", "replay-eurusd-production-capture-20261014.json"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(full.Symbol)); err != nil {
		t.Fatal(err)
	}
	barrierCfg, err := engine.BarrierConfigFromConfig(doc, settings.Geometry)
	if err != nil {
		t.Fatal(err)
	}
	books := func(capture *replaycapture.Capture, until int64) map[int64]string {
		out := map[int64]string{}
		_, err := replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
			if e.Timeframe != market.M5 || e.BarTime > until || e.Context == nil {
				return
			}
			m5 := e.Context.Timeframes[market.M5]
			if m5 == nil || len(m5.Candles) < 150 {
				return
			}
			atr, aerr := indicator.CanonicalATR(m5.Candles, settings.ATR.Length, settings.ATR.Algorithm)
			if aerr != nil {
				return
			}
			ss := structure.Update(m5.Candles, atr, market.M5, settings.Structure)
			zones := map[market.Timeframe]zone.ZoneState{market.M5: zone.Update(m5.Candles, atr, ss.Swings, ss.Breaks, market.M5, settings.Zone)}
			for _, tf := range []market.Timeframe{market.M15, market.H1} {
				if ctx := e.Context.Timeframes[tf]; ctx != nil {
					zones[tf] = ctx.Zones
				}
			}
			raw, merr := json.Marshal(redistransport.BuildZoneBook(e.Symbol, zones, e.BarTime, &barrierCfg).Barriers)
			if merr != nil {
				t.Fatal(merr)
			}
			out[e.BarTime] = string(raw)
		}})
		if err != nil {
			t.Fatal(err)
		}
		return out
	}
	m5 := full.Timeframes["M5"]
	cut := int64(m5[len(m5)*7/10][0])
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
	want, got := books(full, cut), books(&truncated, cut)
	if len(want) < 100 {
		t.Fatalf("the full replay produced only %d barrier books up to the cut", len(want))
	}
	differ := 0
	for bar, book := range want {
		if other, ok := got[bar]; !ok || other != book {
			differ++
			if differ <= 5 {
				t.Errorf("bar %d: the barrier book differs once later bars are removed", bar)
			}
		}
	}
	for bar := range got {
		if _, ok := want[bar]; !ok {
			differ++
			t.Errorf("bar %d: a book exists only without the later bars", bar)
		}
	}
	t.Logf("%d barrier books up to the cut compared; %d differ", len(want), differ)
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
