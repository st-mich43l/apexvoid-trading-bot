package engine

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

const configDir = "../../../config"

// copyConfig writes the real configuration to a temp dir with analysis.yml's require_candle_confirmation leaf rewritten (replacement "" removes it).
func copyConfig(t *testing.T, replacement string) string {
	t.Helper()
	dst := t.TempDir()
	err := filepath.WalkDir(configDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(configDir, path)
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		if rel == "analysis.yml" {
			var lines []string
			for _, line := range strings.Split(string(data), "\n") {
				if strings.HasPrefix(line, "  require_candle_confirmation:") {
					if replacement == "" {
						continue
					}
					line = "  require_candle_confirmation: " + replacement
				}
				lines = append(lines, line)
			}
			data = []byte(strings.Join(lines, "\n"))
		}
		return os.WriteFile(target, data, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Join(dst, "apexvoid.yml")
}

func settingsFor(t *testing.T, root string) Settings {
	t.Helper()
	doc, err := config.ResolveDocument(root)
	if err != nil {
		t.Fatal(err)
	}
	settings, err := LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := ApplyInstrument(&settings, doc, "XAU"); err != nil {
		t.Fatal(err)
	}
	return settings
}

// One analysis leaf decides for every strategy.
func TestRequireCandleConfirmationReachesEveryStrategy(t *testing.T) {
	cases := []struct {
		name, leaf string
		want       bool
	}{
		{"false", "false", false},
		{"true", "true", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			settings := settingsFor(t, copyConfig(t, c.leaf))
			if settings.RequireCandleConfirmation != c.want {
				t.Fatalf("Settings.RequireCandleConfirmation = %v, want %v", settings.RequireCandleConfirmation, c.want)
			}
			if len(settings.Strategies) == 0 {
				t.Fatal("no strategies configured")
			}
			for _, s := range settings.Strategies {
				if got, ok := s.Parameters["require_candle_confirmation"].(bool); !ok || got != c.want {
					t.Errorf("%s: require_candle_confirmation = %v, want %v", s.ID, s.Parameters["require_candle_confirmation"], c.want)
				}
			}
		})
	}
}

// Like every other analysis leaf the engine reads, the switch is required: a configuration without it fails to load instead of silently choosing a mode.
func TestAMissingCandleConfirmationLeafFailsLoudly(t *testing.T) {
	doc, err := config.ResolveDocument(copyConfig(t, ""))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := LoadSettings(doc, market.M5, false); err == nil || !strings.Contains(err.Error(), "analysis.require_candle_confirmation") {
		t.Fatalf("LoadSettings error = %v, want one naming analysis.require_candle_confirmation", err)
	}
}

func TestTheCheckedInConfigurationTurnsCandleConfirmationOff(t *testing.T) {
	if settings := settingsFor(t, filepath.Join(configDir, "apexvoid.yml")); settings.RequireCandleConfirmation {
		t.Fatal("config/analysis.yml analysis.require_candle_confirmation is not false")
	}
}
