package detectorparity_test

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

func TestDump(t *testing.T) {
	out := os.Getenv("DETECTOR_DUMP_DIR")
	if out == "" {
		t.Skip("exploration only")
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"replay-xau-production-capture-20260921.json", "replay-gbpusd-production-capture-20261005.json", "replay-usdjpy-production-capture-20261005.json"} {
		c, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
		if err != nil {
			t.Fatal(err)
		}
		f, _ := os.Create(filepath.Join(out, string(c.Symbol)+".jsonl"))
		enc := json.NewEncoder(f)
		_, err = replaycapture.Replay(doc, c, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
			if e.Timeframe != market.M5 {
				return
			}
			tf := e.Context.Timeframes[market.M5]
			facts := map[string]any{"bar": e.BarTime, "kind": "facts", "bias": e.Context.Bias.Direction, "regime": tf.Regime.Kind, "n_swings": len(tf.Structure.Swings), "n_levels": len(tf.KeyLevel.Levels), "n_trendlines": len(tf.Trendline.Lines), "price": tf.Candles[len(tf.Candles)-1].Close}
			if tf.Fib.Range != nil {
				facts["pd_zone"], facts["pd_position"] = tf.Fib.Range.Zone, tf.Fib.Range.Position
			}
			var tls []map[string]any
			for _, l := range tf.Trendline.Lines {
				tls = append(tls, map[string]any{"kind": l.Kind.String(), "broken": l.BrokenAt != nil, "state": l.State.String(), "n": len(l.ValidationTouches) + 2})
			}
			facts["trendlines"] = tls
			_ = enc.Encode(facts)
			for _, cand := range e.Candidates {
				rec := map[string]any{"bar": e.BarTime, "strategy": cand.Strategy, "direction": cand.Direction, "low": cand.Entry.Low, "high": cand.Entry.High, "sid": cand.StructuralID}
				if cand.Technical != nil && cand.Technical.Confluence != nil {
					rec["confluence"] = cand.Technical.Confluence.SelectedStars
				}
				if cand.Reaction != nil {
					rec["touch"], rec["confirm"] = cand.Reaction.TouchBarTime, cand.Reaction.ConfirmationBarTime
				}
				_ = enc.Encode(rec)
			}
		}})
		if err != nil {
			t.Fatal(err)
		}
		f.Close()
	}
}
