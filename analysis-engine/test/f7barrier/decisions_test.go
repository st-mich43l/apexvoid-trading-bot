package f7barrier_test

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// TestDumpDecisionInputs records, for EVERY closed M5 bar of the requested captures that produced at least one opportunity,
// everything an admission decision is built from: the opportunities the strategies emitted (full-struct hash plus the
// geometry fields) and the zone book the publisher would write (barriers exactly as BuildZoneBook returns them). Run it on
// two revisions and compare with compare_decisions.py to prove a change moved no decision.
//
//	F7_DECISIONS_DIR=/tmp/before F7_CAPTURES=<a.json,b.json> go test ./test/f7barrier -run TestDumpDecisionInputs
func TestDumpDecisionInputs(t *testing.T) {
	dir := os.Getenv("F7_DECISIONS_DIR")
	if dir == "" {
		t.Skip("F7_DECISIONS_DIR not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
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
			barrierCfg, bErr := engine.BarrierConfigFromConfig(doc, settings.Geometry)
			if bErr != nil {
				t.Error(bErr)
				return
			}
			var records []m
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				if e.Timeframe != market.M5 || e.Context == nil || len(e.Candidates) == 0 {
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
				book := redistransport.BuildZoneBook(e.Symbol, books, e.BarTime, &barrierCfg)
				cands := make([]m, 0, len(e.Candidates))
				for _, c := range e.Candidates {
					raw, merr := json.Marshal(c)
					if merr != nil {
						t.Error(merr)
						return
					}
					sum := sha256.Sum256(raw)
					targets := make([]float64, 0, len(c.Targets))
					for _, tg := range c.Targets {
						targets = append(targets, float64(tg.Price.Price))
					}
					rec := m{"id": c.ID, "strategy": string(c.Strategy), "version": c.StrategyVersion, "direction": string(c.Direction),
						"entry_low": float64(c.Entry.Low), "entry_high": float64(c.Entry.High), "invalidation": float64(c.Invalidation.Price),
						"targets": targets, "quality": c.Quality.Overall, "observed_tf": string(c.ObservedTimeframe), "sha256": hex.EncodeToString(sum[:])}
					if c.Technical != nil {
						rec["atr"], rec["reference_price"] = c.Technical.ATR, c.Technical.ReferencePrice
						rec["confirmed_reaction"] = c.Technical.Confirmation != nil
					}
					if c.Strategy == "range_edge" {
						if frame := m5.Legacy; frame != nil && frame.ScalpRange != nil {
							sr := frame.ScalpRange
							edge := func(b techniquezone.ScalpBarrier) m {
								first := int64(0)
								if b.FirstTouchIndex >= 0 && b.FirstTouchIndex < len(frame.Bars) {
									first = frame.Bars[b.FirstTouchIndex].Time
								}
								return m{"level": b.Level, "low": b.Low, "high": b.High, "touches": b.Touches, "first_touch_time": first}
							}
							rec["range"] = m{"lower": edge(sr.Lower), "upper": edge(sr.Upper), "equilibrium": sr.Equilibrium, "state": sr.State}
						}
					}
					if c.StopEnvelope != nil {
						rec["stop_envelope"] = c.StopEnvelope
					}
					cands = append(cands, rec)
				}
				records = append(records, m{"symbol": string(e.Symbol), "bar": e.BarTime, "capture": name, "price": candles[len(candles)-1].Close,
					"candidates": cands, "barriers": book.Barriers})
			}})
			if err != nil {
				t.Error(err)
				return
			}
			sort.SliceStable(records, func(i, j int) bool { return records[i]["bar"].(int64) < records[j]["bar"].(int64) })
			out, err := os.Create(filepath.Join(dir, name+".decisions.jsonl"))
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
