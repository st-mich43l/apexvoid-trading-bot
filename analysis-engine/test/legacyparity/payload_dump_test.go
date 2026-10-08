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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// TestDumpPayloads writes the first Kafka opportunity payload of every strategy a
// capture produces (PAYLOAD_DUMP=out.json, PAYLOAD_CAPTURE, PAYLOAD_TF=M1|M5), for
// feeding the real Go output through Algo Bot's adapter in a cross-language check.
func TestDumpPayloads(t *testing.T) {
	out := os.Getenv("PAYLOAD_DUMP")
	if out == "" {
		t.Skip()
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	c, err := replaycapture.Load(filepath.Join("..", "..", "testdata", os.Getenv("PAYLOAD_CAPTURE")))
	if err != nil {
		t.Fatal(err)
	}
	tf := market.Timeframe(os.Getenv("PAYLOAD_TF"))
	seen := map[string]bool{}
	payloads := map[string]any{}
	_, err = replaycapture.Replay(doc, c, tf, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
		if e.Timeframe != tf {
			return
		}
		for _, cand := range e.Candidates {
			if cand.Reaction == nil && string(cand.Strategy) != "box_breakout" && tf == market.M5 || seen[string(cand.Strategy)] {
				continue
			}
			seen[string(cand.Strategy)] = true
			payloads[string(cand.Strategy)] = kafka.OpportunityPayloadFromCandidate(cand, kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"})
		}
	}})
	if err != nil {
		t.Fatal(err)
	}
	data, _ := json.Marshal(payloads)
	if err := os.WriteFile(out, data, 0o644); err != nil {
		t.Fatal(err)
	}
}
