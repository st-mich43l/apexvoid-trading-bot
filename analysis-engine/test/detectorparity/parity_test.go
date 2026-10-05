package detectorparity_test

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

type oracleFact struct {
	Symbol          string  `json:"symbol"`
	Timeframe       string  `json:"timeframe"`
	Bar             int64   `json:"bar"`
	Strategy        string  `json:"strategy"`
	Direction       string  `json:"direction"`
	EntryLow        float64 `json:"entry_low"`
	EntryHigh       float64 `json:"entry_high"`
	KeyLevel        float64 `json:"key_level"`
	Confirmation    string  `json:"confirmation"`
	TouchBar        string  `json:"touch_bar"`
	ConfirmationBar string  `json:"confirmation_bar"`
	SourceTouches   int     `json:"source_touches"`
	SourceScore     float64 `json:"source_score"`
}

type observedCandidate struct {
	candidate opportunity.Candidate
	bar       int64
}

func TestDetectorReplayHasOracleCoveredStrategies(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	replays := []struct {
		name string
		file string
	}{
		{"XAU", "replay-xau-production-capture-20260921.json"},
		{"GBPUSD", "replay-gbpusd-production-capture-20261005.json"},
		{"USDJPY", "replay-usdjpy-production-capture-20261005.json"},
	}
	wanted := map[string]bool{
		"break_retest": true, "range_edge": true, "snap_back": true,
		"momentum_ride": true, "fade_scalp": true,
	}
	for _, replay := range replays {
		capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", replay.file))
		if err != nil {
			t.Fatal(err)
		}
		counts := map[string]int{}
		var observed []observedCandidate
		_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{
			OnEvaluation: func(e engine.Evaluation) {
				if e.Timeframe != market.M5 {
					return
				}
				for _, candidate := range e.Candidates {
					if wanted[string(candidate.Strategy)] {
						counts[string(candidate.Strategy)]++
						observed = append(observed, observedCandidate{candidate: candidate, bar: e.BarTime})
					}
				}
			},
		})
		if err != nil {
			t.Fatalf("%s replay: %v", replay.name, err)
		}
		for strategy := range wanted {
			if counts[strategy] == 0 {
				t.Errorf("%s: no detector-level candidate for %s", replay.name, strategy)
			}
		}
		if replay.name == "XAU" {
			assertXAUOracleFact(t, observed)
		}
	}
}

func assertXAUOracleFact(t *testing.T, candidates []observedCandidate) {
	t.Helper()
	var raw oracleFact
	data, err := os.ReadFile(filepath.Join("..", "..", "testdata", "detector-parity-oracle-xau.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatal(err)
	}
	for _, observed := range candidates {
		candidate := observed.candidate
		if string(candidate.Strategy) != raw.Strategy || observed.bar != raw.Bar {
			continue
		}
		if string(candidate.Direction) != raw.Direction {
			t.Fatalf("XAU oracle direction drift at %d: got %s want %s", raw.Bar, candidate.Direction, raw.Direction)
		}
		if math.Abs(float64(candidate.Entry.Low)-raw.EntryLow) > .01 || math.Abs(float64(candidate.Entry.High)-raw.EntryHigh) > .01 {
			t.Fatalf("XAU oracle entry drift at %d: got %.5f-%.5f want %.5f-%.5f", raw.Bar, candidate.Entry.Low, candidate.Entry.High, raw.EntryLow, raw.EntryHigh)
		}
		if candidate.Reaction == nil || candidate.Reaction.Pattern != raw.Confirmation || candidate.Reaction.TouchBarTime != 1789989000 || candidate.Reaction.ConfirmationBarTime != 1789998300 {
			t.Fatalf("XAU oracle reaction drift at %d: %+v", raw.Bar, candidate.Reaction)
		}
		return
	}
	t.Fatalf("XAU oracle candidate %s at %d was not observed", raw.Strategy, raw.Bar)
}
