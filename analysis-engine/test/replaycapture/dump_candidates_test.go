package replaycapture_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// TestDumpCandidatesForOfflineArbitrationEvaluation writes every candidate the engine
// emits on every closed bar of every committed capture, as JSON lines, for
// tools/arbitration_eval. It does nothing unless ARBITRATION_DUMP_DIR is set.
//
//	ARBITRATION_DUMP_DIR=/tmp/arb go test ./test/replaycapture -run TestDumpCandidates
//
// The dump is a deterministic function of the capture and the committed config. It carries
// the published facts only (quality, confluence, evidence, geometry); it never contains an
// outcome.
func TestDumpCandidatesForOfflineArbitrationEvaluation(t *testing.T) {
	dir := os.Getenv("ARBITRATION_DUMP_DIR")
	if dir == "" {
		t.Skip("ARBITRATION_DUMP_DIR not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	captures := []string{
		"replay-xau-m1-production-capture-20261006.json",
		"replay-xau-production-capture-20260921.json",
		"replay-eurusd-production-capture-20261006.json",
		"replay-gbpusd-production-capture-20261005.json",
		"replay-gbpjpy-production-capture-20261006.json",
		"replay-usdjpy-production-capture-20261005.json",
	}
	var wg sync.WaitGroup
	for _, name := range captures {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
			if err != nil {
				t.Error(err)
				return
			}
			var rows []map[string]any
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				for _, c := range e.Candidates {
					row := map[string]any{
						"capture": name, "symbol": string(c.Symbol), "bar_time": e.BarTime, "bar_tf": string(e.Timeframe),
						"id": c.ID, "strategy": string(c.Strategy), "direction": string(c.Direction),
						"entry_low": c.Entry.Low, "entry_high": c.Entry.High,
						"invalidation": float64(c.Invalidation.Price),
						"quality":      c.Quality.Overall, "quality_components": c.Quality.Components,
						"structural_id": c.StructuralID, "created_at": c.CreatedAt, "expires_at": c.ExpiresAt,
						"observed_tf": string(c.ObservedTimeframe), "structure_tf": string(c.StructureTimeframe),
						"has_reaction":            c.Reaction != nil,
						"has_detector_confluence": c.DetectorConfluence != nil,
					}
					var targets []float64
					for _, target := range c.Targets {
						targets = append(targets, float64(target.Price.Price))
					}
					row["targets"] = targets
					var evidence []string
					for _, ev := range c.Evidence {
						evidence = append(evidence, ev.Code)
					}
					row["evidence"] = evidence
					if c.StopEnvelope != nil {
						row["stop_floor_pips"], row["stop_cap_pips"] = c.StopEnvelope.FloorPips, c.StopEnvelope.CapPips
					}
					if tech := c.Technical; tech != nil {
						row["atr"], row["reference_price"], row["reference_time"] = tech.ATR, tech.ReferencePrice, tech.ReferenceTime
						row["bias"] = string(tech.BiasDirection)
						var htf []map[string]string
						for _, h := range tech.HigherTimeframes {
							htf = append(htf, map[string]string{"tf": string(h.Timeframe), "direction": string(h.Direction)})
						}
						row["htf"] = htf
						if cf := tech.Confluence; cf != nil {
							row["confluence"] = map[string]any{
								"selected_stars": cf.SelectedStars, "v1_stars": cf.V1Stars, "v2_stars": cf.V2Stars, "v2_raw": cf.V2Raw,
								"raw_factor": cf.RawFactorScore, "zone_quality": cf.ZoneQualityScore, "mad_bonus": cf.MADBonus,
								"htf_aligned": cf.Factors.HTFAligned, "touches": cf.Factors.Touches, "wick": cf.Factors.WickRejection,
								"displacement": cf.Factors.DisplacementGrade, "session": cf.Factors.SessionContext,
								"structural": cf.Factors.StructuralAgreement, "fib": cf.Factors.FibTouch, "choch": cf.Factors.CHoCH,
							}
						}
						if tech.CandleEvidence != nil {
							row["candle_score"] = tech.CandleEvidence.FinalScore
						}
					}
					rows = append(rows, row)
				}
			}})
			if err != nil {
				t.Error(err)
				return
			}
			sort.SliceStable(rows, func(i, j int) bool {
				if rows[i]["bar_time"].(int64) != rows[j]["bar_time"].(int64) {
					return rows[i]["bar_time"].(int64) < rows[j]["bar_time"].(int64)
				}
				return rows[i]["id"].(string) < rows[j]["id"].(string)
			})
			out, err := os.Create(filepath.Join(dir, name+".jsonl"))
			if err != nil {
				t.Error(err)
				return
			}
			defer out.Close()
			enc := json.NewEncoder(out)
			for _, row := range rows {
				if err := enc.Encode(row); err != nil {
					t.Error(err)
					return
				}
			}
			t.Logf("%s: %d candidates", name, len(rows))
		}(name)
	}
	wg.Wait()
}
