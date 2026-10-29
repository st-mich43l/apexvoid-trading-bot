// Package f7barrier_test dumps, for a list of requested M5 bars of committed captures, the opposing-barrier book the
// Go engine publishes at that bar (Model C of `git show f02d62f:docs/architecture/f7-opposing-barrier-ground-truth.md`) together with the
// raw zones it was built from, so tools/f7_barrier_replay.py can evaluate the real Algo Bot target-room code against it.
//
//	F7_BARS=/tmp/f7/bars.json F7_CAPTURES=replay-xau-production-capture-20261014.json F7_DUMP_DIR=/tmp/f7 \
//	    go test ./test/f7barrier -run TestDumpBooks
//
// F7_BARS maps a symbol to the M5 bar-open times (Unix seconds) to dump. The book is built exactly as the production
// publisher builds it: the V2 zone state of M5, M15 and H1, then redistransport.BuildZoneBook with the instrument's
// barrier policy. Read-only: nothing here changes engine behavior, and the oracle (frozen Python) never sees this output.
package f7barrier_test

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
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

type m map[string]any

// nearAtr is how far from price (in ATR of the zone's timeframe) a raw zone is still worth recording.
const nearAtr = 12.0

func rawZones(books map[market.Timeframe]zone.ZoneState, price float64) []m {
	out := []m{}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		st, ok := books[tf]
		if !ok {
			continue
		}
		for _, z := range st.Zones {
			if z.Kind != zone.KindSupply && z.Kind != zone.KindDemand {
				continue
			}
			lo, hi := float64(z.Low), float64(z.High)
			if st.ATR > 0 && (lo-price > nearAtr*st.ATR || price-hi > nearAtr*st.ATR) {
				continue
			}
			out = append(out, m{"id": z.ID, "tf": string(tf), "kind": z.Kind.String(), "side": z.Side.String(), "low": lo, "high": hi,
				"state": z.State.String(), "strength": z.Strength, "legacy_score": z.LegacyScore, "touches": z.TouchCount,
				"relevance": int(z.Relevance), "layer": z.Layer.String(), "origin_time": z.OriginTime, "created_at": z.CreatedAt,
				"last_touched_at": z.LastTouchedAt, "atr": st.ATR})
		}
	}
	return out
}

func TestDumpBooks(t *testing.T) {
	dir := os.Getenv("F7_DUMP_DIR")
	if dir == "" {
		t.Skip("F7_DUMP_DIR not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	var wanted map[string][]int64
	raw, err := os.ReadFile(os.Getenv("F7_BARS"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &wanted); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for _, name := range strings.Split(os.Getenv("F7_CAPTURES"), ",") {
		if name == "" {
			continue
		}
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
			if err != nil {
				t.Error(err)
				return
			}
			settings, err := engine.LoadSettings(doc, market.M5, false)
			if err != nil {
				t.Error(err)
				return
			}
			if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
				t.Error(err)
				return
			}
			want := map[int64]bool{}
			for _, b := range wanted[string(capture.Symbol)] {
				want[b] = true
			}
			barrierCfg, bErr := engine.BarrierConfigFromConfig(doc, settings.Geometry)
			if bErr != nil {
				t.Error(bErr)
				return
			}
			var records []m
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				if e.Timeframe != market.M5 || !want[e.BarTime] || e.Context == nil {
					return
				}
				m5 := e.Context.Timeframes[market.M5]
				if m5 == nil || len(m5.Candles) < 150 {
					return
				}
				candles := m5.Candles
				atr, err := indicator.CanonicalATR(candles, settings.ATR.Length, settings.ATR.Algorithm)
				if err != nil {
					return
				}
				ss := structure.Update(candles, atr, market.M5, settings.Structure)
				books := map[market.Timeframe]zone.ZoneState{market.M5: zone.Update(candles, atr, ss.Swings, ss.Breaks, market.M5, settings.Zone)}
				for _, tf := range []market.Timeframe{market.M15, market.H1} {
					if ctx := e.Context.Timeframes[tf]; ctx != nil {
						books[tf] = ctx.Zones
					}
				}
				price := candles[len(candles)-1].Close
				book := redistransport.BuildZoneBook(e.Symbol, books, e.BarTime, &barrierCfg)
				rec := m{"symbol": string(e.Symbol), "bar": e.BarTime, "capture": name, "price": price, "atr": atr[len(atr)-1],
					"history_bars_m5": len(candles), "barriers": book.Barriers, "raw_zones": rawZones(books, price)}
				if m15 := e.Context.Timeframes[market.M15]; m15 != nil && m15.Legacy != nil {
					rec["legacy_zones"] = legacyZones(e, price)
				}
				records = append(records, rec)
			}})
			if err != nil {
				t.Error(err)
				return
			}
			sort.SliceStable(records, func(i, j int) bool { return records[i]["bar"].(int64) < records[j]["bar"].(int64) })
			out, err := os.Create(filepath.Join(dir, name+".go.jsonl"))
			if err != nil {
				t.Error(err)
				return
			}
			defer out.Close()
			enc := json.NewEncoder(out)
			for _, r := range records {
				if err := enc.Encode(r); err != nil {
					t.Error(err)
					return
				}
			}
		}(name)
	}
	wg.Wait()
}

// legacyZones is the legacy-parity technique zone population the strategies read (M5/M15/H1), near price.
func legacyZones(e engine.Evaluation, price float64) []m {
	out := []m{}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		ctx := e.Context.Timeframes[tf]
		if ctx == nil || ctx.Legacy == nil {
			continue
		}
		atr := ctx.Legacy.DetectorATR
		for _, z := range ctx.Legacy.TechniqueZones {
			if atr > 0 && (z.Bottom-price > nearAtr*atr || price-z.Top > nearAtr*atr) {
				continue
			}
			if math.IsNaN(z.Score) {
				continue
			}
			out = append(out, m{"tf": string(tf), "side": z.Side, "low": z.Bottom, "high": z.Top, "score": z.Score, "touches": z.Touches,
				"mitigated": z.Mitigated, "source": z.Source, "sources": z.Sources, "origin_index": z.OriginIndex})
		}
	}
	return out
}
