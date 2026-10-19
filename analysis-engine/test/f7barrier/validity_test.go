package f7barrier_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// TestMeasureLiveZoneValidity asks, for every live supply/demand zone the book would publish at every 6th M5 bar, whether the zone's own
// structural-validity rule (zone.NotInvalidated) still holds. A zone the lifecycle calls live while that rule says it is broken is a
// zone the barrier book treats as a standing wall although price has already accepted beyond it.
//
//	F7_VALIDITY_OUT=/tmp/validity.json F7_CAPTURES=<a.json,b.json> go test ./test/f7barrier -run TestMeasureLiveZoneValidity
func TestMeasureLiveZoneValidity(t *testing.T) {
	out := os.Getenv("F7_VALIDITY_OUT")
	if out == "" {
		t.Skip("F7_VALIDITY_OUT not set")
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	type tally map[string]int
	total := tally{}
	var examples []m
	var mu sync.Mutex
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
			local := tally{}
			var localEx []m
			n := 0
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				if e.Timeframe != market.M5 || e.Context == nil {
					return
				}
				n++
				if n%6 != 0 {
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
				states := map[market.Timeframe]zone.ZoneState{market.M5: zone.Update(candles, atr, ss.Swings, ss.Breaks, market.M5, settings.Zone)}
				frames := map[market.Timeframe][]market.Candle{market.M5: candles}
				for _, tf := range []market.Timeframe{market.M15, market.H1} {
					if ctx := e.Context.Timeframes[tf]; ctx != nil {
						states[tf] = ctx.Zones
						frames[tf] = ctx.Candles
					}
				}
				for tf, st := range states {
					for _, z := range st.Zones {
						if z.Kind != zone.KindSupply && z.Kind != zone.KindDemand {
							continue
						}
						if z.State != zone.StateFresh && z.State != zone.StateTouched && z.State != zone.StatePartiallyMitigated {
							continue
						}
						key := string(tf) + ":" + z.State.String()
						local["live:"+key]++
						valid := zone.NotInvalidated(frames[tf], z.BreakIndex+1, z, st.ATR, settings.Zone.Lifecycle)
						if !valid {
							local["live_but_broken:"+key]++
							if len(localEx) < 5 {
								localEx = append(localEx, m{"capture": name, "bar": e.BarTime, "tf": string(tf), "id": z.ID, "side": z.Side.String(),
									"low": float64(z.Low), "high": float64(z.High), "state": z.State.String(), "touches": z.TouchCount, "break_index": z.BreakIndex, "n_candles": len(frames[tf])})
							}
						}
					}
				}
			}})
			if err != nil {
				t.Error(err)
				return
			}
			mu.Lock()
			for k, v := range local {
				total[k] += v
			}
			examples = append(examples, localEx...)
			mu.Unlock()
		}(name)
	}
	wg.Wait()
	raw, _ := json.MarshalIndent(m{"totals": total, "examples": examples}, "", " ")
	if err := os.WriteFile(out, raw, 0o644); err != nil {
		t.Fatal(err)
	}
}
