package czreplay_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The optional XAU containment patch (docs/strategies/confluence-zone-v3-xau-containment.patch)
// is NOT applied to config/instruments.yml. This proves what it would do, on a copy:
// it contains confluence_zone on XAU alone and changes nothing else.
func TestOptionalXAUContainmentPatchDoesOnlyWhatItSays(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not available")
	}
	patch, err := filepath.Abs(repoFile("docs", "strategies", "confluence-zone-v3-xau-containment.patch"))
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	if err := copyDir(repoFile("config"), filepath.Join(dir, "config")); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("git", "apply", patch)
	cmd.Dir = dir
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("the patch does not apply to the committed configuration: %v\n%s", err, out)
	}
	observe := func(root, symbol string) map[string]bool {
		doc, err := config.ResolveDocument(filepath.Join(root, "apexvoid.yml"))
		if err != nil {
			t.Fatal(err)
		}
		settings, err := engine.LoadSettings(doc, market.M5, false)
		if err != nil {
			t.Fatal(err)
		}
		if err := engine.ApplyInstrument(&settings, doc, symbol); err != nil {
			t.Fatal(err)
		}
		out := map[string]bool{}
		for id, v := range settings.ObserveOnly {
			out[string(id)] = v
		}
		return out
	}
	for _, symbol := range []string{"XAU", "EURUSD", "GBPUSD", "GBPJPY", "USDJPY"} {
		before := observe(repoFile("config"), symbol)
		after := observe(filepath.Join(dir, "config"), symbol)
		if symbol == "XAU" {
			if !after["confluence_zone"] || len(after) != len(before)+1 {
				t.Fatalf("XAU observe-only after the patch = %v (before %v)", after, before)
			}
			for id := range before {
				if !after[id] {
					t.Fatalf("the patch removed %s from XAU's list", id)
				}
			}
			continue
		}
		if len(after) != len(before) || after["confluence_zone"] {
			t.Fatalf("%s must be untouched: %v -> %v", symbol, before, after)
		}
	}
}

func copyDir(from, to string) error {
	return filepath.Walk(from, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(from, path)
		target := filepath.Join(to, rel)
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, raw, 0o644)
	})
}
