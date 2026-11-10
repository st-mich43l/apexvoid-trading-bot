package engine

import (
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Range Scalp and Impulse Retrace measure their room against the same opposing-barrier policy the Algo Bot's target-room check reads: the execution-zone
// width limits and the instrument's barrier buffer (XAU overrides the global 0.5 with 0.15).
func TestScalpRoomPolicyResolvesFromTheRealConfiguration(t *testing.T) {
	settings := settingsFor(t, filepath.Join(configDir, "apexvoid.yml"))
	seen := map[string]bool{}
	for _, s := range settings.Strategies {
		id := string(s.ID)
		if id != "range_scalp" && id != "impulse_retrace" {
			if _, injected := s.Parameters["barrier_buffer_atr"]; injected {
				t.Errorf("%s received the scalp room policy: it is not a room-measuring scalp", id)
			}
			continue
		}
		seen[id] = true
		for key, want := range map[string]float64{"barrier_buffer_atr": 0.15, "barrier_max_width_atr": 2.0, "barrier_max_width_pips": 100.0} {
			if got := s.Parameters[key]; got != want {
				t.Errorf("%s %s = %v, want %v", id, key, got, want)
			}
		}
	}
	if !seen["range_scalp"] || !seen["impulse_retrace"] {
		t.Fatalf("scalp strategies not configured: %v", seen)
	}
}

func TestScalpRoomBufferFallsBackToTheGlobalLeafBeforeAnInstrumentIsApplied(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join(configDir, "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range settings.Strategies {
		if s.ID == "range_scalp" {
			if got := s.Parameters["barrier_buffer_atr"]; got != 0.5 {
				t.Errorf("before an instrument is applied the buffer is %v, want the global 0.5", got)
			}
		}
	}
}
