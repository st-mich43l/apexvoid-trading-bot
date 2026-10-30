package demand_test

import (
	"math"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/demand"
)

// The zone score the strategy owns must reproduce, zone for zone, the score the shared zone chain gave (techniquezone.ScoreZones on the frame's unmerged
// technique zones), before the premium/discount reading is corrected: same facts in, same points out. Every closed M5 bar of every committed capture,
// every zone (both sides) of the M5, M15 and H1 frames.
func TestTheOwnedZoneScoreReproducesTheSharedOneOnEveryCapture(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var paths []string
	for _, pattern := range []string{"replay-*-production-capture-*.json", "obwindow-*.json", "fvgwindow-*.json"} {
		matches, _ := filepath.Glob(filepath.Join("..", "..", "..", "testdata", pattern))
		for _, m := range matches {
			if !strings.Contains(m, "-m1-") {
				paths = append(paths, m)
			}
		}
	}
	sort.Strings(paths)
	if len(paths) < 21 {
		t.Fatalf("expected the committed captures, found %d", len(paths))
	}
	var mu sync.Mutex
	compared, mismatches := 0, 0
	var wg sync.WaitGroup
	gate := make(chan struct{}, runtime.NumCPU())
	for _, path := range paths {
		wg.Add(1)
		go func(path string) {
			defer wg.Done()
			gate <- struct{}{}
			defer func() { <-gate }()
			capture, err := replaycapture.Load(path)
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
			var cfg strategy.Config
			for _, c := range settings.Strategies {
				if c.ID == "demand" {
					cfg = c
				}
			}
			s, err := demand.New(cfg)
			if err != nil {
				t.Error(err)
				return
			}
			owner := s.(*demand.Strategy)
			local, bad := 0, 0
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				if e.Timeframe != market.M5 || e.Context == nil {
					return
				}
				for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
					frameContext := e.Context.Timeframes[tf]
					if frameContext == nil || frameContext.Legacy == nil {
						continue
					}
					type key struct {
						side      string
						origin    int
						low, high float64
					}
					shared := map[key]float64{}
					for _, z := range frameContext.Legacy.TechniqueZones {
						for _, source := range z.Sources {
							if source == "supply_demand" {
								side := "sell"
								if z.Side == "demand" {
									side = "buy"
								}
								shared[key{side, z.OriginIndex, z.Bottom, z.Top}] = z.Score
							}
						}
					}
					for _, z := range owner.ZoneScores(e.Context, tf) {
						want, ok := shared[key{z.Side, z.Origin, z.Low, z.High}]
						local++
						if !ok || math.Abs(want-z.Score) > 1e-9 {
							bad++
						}
					}
				}
			}})
			if err != nil {
				t.Error(err)
			}
			mu.Lock()
			compared += local
			mismatches += bad
			mu.Unlock()
		}(path)
	}
	wg.Wait()
	t.Logf("%d zone scores compared, %d differ", compared, mismatches)
	if compared == 0 {
		t.Fatal("no zone was compared: the test proves nothing")
	}
	if mismatches != 0 {
		t.Fatalf("%d of %d zone scores differ from the shared score", mismatches, compared)
	}
}
