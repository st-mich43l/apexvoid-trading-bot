package architecture_test

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The Key Level LevelSource hook replaces the levels one strategy reads, for measurement (docs/strategies/keylevel-edge-source.md). Like replaycapture's
// WithoutStopBudget it must have no production caller: only the plumbing that carries it (the strategy that defines it, the engine settings and worker that pass it on, the
// replaycapture option that sets it) may mention it; no command, service or other package may.
func TestKeyLevelSourceHasNoProductionCaller(t *testing.T) {
	allowed := map[string]bool{
		"internal/strategy/keylevel/keylevel.go": true,
		"internal/engine/settings.go":            true,
		"internal/engine/worker.go":              true,
		"internal/replaycapture/replay.go":       true,
	}
	root := filepath.Join("..", "..")
	var offenders []string
	found := map[string]bool{}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		text := string(raw)
		if !strings.Contains(text, "KeyLevelSource") && !strings.Contains(text, "UseLevelSource") {
			return nil
		}
		rel, _ := filepath.Rel(root, path)
		rel = filepath.ToSlash(rel)
		if allowed[rel] {
			found[rel] = true
			return nil
		}
		offenders = append(offenders, rel)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(offenders) > 0 {
		t.Errorf("the Key Level LevelSource hook is test-only; production files that mention it: %v", offenders)
	}
	for file := range allowed {
		if !found[file] {
			t.Errorf("%s no longer carries the hook: update this test's allow-list if the plumbing moved", file)
		}
	}
}
