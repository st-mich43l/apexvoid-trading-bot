package legacyparity_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// TestDumpCandidates is an analysis tool, not a check: it dumps the candidates of a
// capture (all strategies, or KL_STRATEGY) with the facts the engine saw, one JSON
// line each, for attributing live trades to detector facts. It runs only when
// KL_DUMP=out.jsonl and KL_CAPTURE=<file in testdata> are set.
func TestDumpCandidates(t *testing.T) {
	out := os.Getenv("KL_DUMP")
	if out == "" {
		t.Skip()
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	c, err := replaycapture.Load(filepath.Join("..", "..", "testdata", os.Getenv("KL_CAPTURE")))
	if err != nil {
		t.Fatal(err)
	}
	f, _ := os.Create(out)
	defer f.Close()
	enc := json.NewEncoder(f)
	_, err = replaycapture.Replay(doc, c, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != market.M5 {
			return
		}
		frame := e.Context.Timeframes[market.M5].Legacy
		for _, cand := range e.Candidates {
			if string(cand.Strategy) != os.Getenv("KL_STRATEGY") && os.Getenv("KL_STRATEGY") != "" {
				continue
			}
			rec := map[string]any{
				"id": cand.ID, "bar": e.BarTime, "strategy": cand.Strategy, "dir": cand.Direction, "entry_low": cand.Entry.Low, "entry_high": cand.Entry.High,
				"stop": cand.Invalidation.Price, "structural": cand.StructuralID, "created": cand.CreatedAt,
			}
			if len(cand.Targets) > 0 {
				rec["target"] = cand.Targets[0].Price.Price
			}
			if cand.Reaction != nil {
				rec["pattern"] = cand.Reaction.Pattern
				rec["touch"] = cand.Reaction.TouchBarTime
				rec["confirm"] = cand.Reaction.ConfirmationBarTime
			}
			if cand.Technical != nil {
				rec["atr"] = cand.Technical.ATR
				rec["bias"] = cand.Technical.BiasDirection
				if cand.Technical.Confluence != nil {
					rec["stars"] = cand.Technical.Confluence.SelectedStars
					rec["htf_aligned"] = cand.Technical.Confluence.Factors.HTFAligned
					rec["fib"] = cand.Technical.Confluence.Factors.FibTouch
				}
				for _, h := range cand.Technical.HigherTimeframes {
					rec["htf_"+string(h.Timeframe)] = h.Direction
				}
			}
			if frame != nil {
				rec["regime"] = frame.Regime.Kind
				rec["structure"] = frame.Structure
				if frame.Range != nil {
					rec["pd_zone"] = frame.Range.Zone
					rec["pd_pos"] = frame.Range.Position
				}
			}
			if e.Context.Legacy != nil {
				rec["local"] = e.Context.Legacy.LocalStructure
				rec["htf"] = e.Context.Legacy.HTFBias
			}
			_ = enc.Encode(rec)
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
}
