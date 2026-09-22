package config_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
)

// repoConfigPath points at the real repository config/ directory — two
// levels up from analysis-engine/. This mirrors algo-bot/tests/
// test_config_v3_parity.py's own approach exactly (referencing
// config/apexvoid.yml directly rather than a copied fixture): the whole
// point of these tests is proving parity against the real files every
// other language reads, not a frozen copy that could drift from them.
//
// test/config/ and internal/config/ sit at the same depth under
// analysis-engine/ (both two path segments below it), so this relative
// path is unchanged from when this file lived in internal/config/.
func repoConfigPath(parts ...string) string {
	return filepath.Join(append([]string{"..", "..", "..", "config"}, parts...)...)
}

func TestResolveDocumentProductionMatchesKnownGeometry(t *testing.T) {
	doc, err := config.ResolveDocument(repoConfigPath("apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}

	cases := []struct {
		symbol      string
		pipSize     float64
		priceDigits int
	}{
		{"XAU", 0.1, 2},
		{"EURUSD", 0.0001, 5},
		{"GBPUSD", 0.0001, 5},
		{"GBPJPY", 0.01, 3},
		{"USDJPY", 0.01, 3},
	}
	for _, c := range cases {
		t.Run(c.symbol, func(t *testing.T) {
			g, err := doc.GeometryFor(c.symbol)
			if err != nil {
				t.Fatalf("GeometryFor(%s): %v", c.symbol, err)
			}
			if g.PipSize != c.pipSize {
				t.Errorf("PipSize = %v, want %v", g.PipSize, c.pipSize)
			}
			if g.PriceDigits != c.priceDigits {
				t.Errorf("PriceDigits = %d, want %d", g.PriceDigits, c.priceDigits)
			}
		})
	}
}

func TestResolveDocumentUnknownInstrumentFailsClosed(t *testing.T) {
	doc, err := config.ResolveDocument(repoConfigPath("apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}
	if _, err := doc.GeometryFor("DOGEUSD"); err == nil {
		t.Fatal("expected an error for an unrecognized instrument")
	}
}

func TestLiveInstrumentsMatchesEveryDeclaredLiveSymbol(t *testing.T) {
	doc, err := config.ResolveDocument(repoConfigPath("apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}
	live, err := doc.LiveInstruments()
	if err != nil {
		t.Fatalf("LiveInstruments: %v", err)
	}
	want := map[string]bool{"XAU": true, "EURUSD": true, "GBPUSD": true, "GBPJPY": true, "USDJPY": true}
	if len(live) != len(want) {
		t.Fatalf("LiveInstruments() = %v, want exactly %v", live, want)
	}
	for _, symbol := range live {
		if !want[symbol] {
			t.Errorf("unexpected live instrument %q", symbol)
		}
	}
}

func TestResolveDemoEvalRootUsesDemoEvalOverlay(t *testing.T) {
	doc, err := config.ResolveDocument(repoConfigPath("apexvoid.demo-eval.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}
	env, ok := doc.Get("runtime.environment")
	if !ok || env != "demo_eval" {
		t.Fatalf("runtime.environment = %v, want \"demo_eval\"", env)
	}
	// Instrument geometry is untouched by the environment overlay — same
	// XAU pip size either way.
	g, err := doc.GeometryFor("XAU")
	if err != nil {
		t.Fatalf("GeometryFor(XAU): %v", err)
	}
	if g.PipSize != 0.1 {
		t.Errorf("PipSize = %v, want 0.1", g.PipSize)
	}
}

func TestResolveDocumentProductionOverlayIsEmpty(t *testing.T) {
	doc, err := config.ResolveDocument(repoConfigPath("apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}
	env, ok := doc.Get("runtime.environment")
	if !ok || env != "production" {
		t.Fatalf("runtime.environment = %v, want \"production\"", env)
	}
}

func writeFile(t *testing.T, dir, name, content string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
		t.Fatalf("writing %s: %v", name, err)
	}
}

func TestResolveDocumentRejectsUnsupportedVersion(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "apexvoid.yml", "version: 2\nincludes: []\n")
	_, err := config.ResolveDocument(filepath.Join(dir, "apexvoid.yml"))
	if err == nil || !strings.Contains(err.Error(), "unsupported version") {
		t.Fatalf("got %v, want an 'unsupported version' error", err)
	}
}

func TestResolveDocumentRejectsMissingInclude(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "apexvoid.yml", "version: 3\nincludes:\n  - does-not-exist.yml\n")
	_, err := config.ResolveDocument(filepath.Join(dir, "apexvoid.yml"))
	if err == nil || !strings.Contains(err.Error(), "missing include") {
		t.Fatalf("got %v, want a 'missing include' error", err)
	}
}

func TestResolveDocumentRejectsDuplicateInclude(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "a.yml", "foo:\n  bar: 1\n")
	writeFile(t, dir, "apexvoid.yml", "version: 3\nincludes:\n  - a.yml\n  - a.yml\n")
	_, err := config.ResolveDocument(filepath.Join(dir, "apexvoid.yml"))
	if err == nil || !strings.Contains(err.Error(), "duplicate include") {
		t.Fatalf("got %v, want a 'duplicate include' error", err)
	}
}

func TestResolveDocumentRejectsEscapingInclude(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "apexvoid.yml", "version: 3\nincludes:\n  - ../outside.yml\n")
	_, err := config.ResolveDocument(filepath.Join(dir, "apexvoid.yml"))
	if err == nil || !strings.Contains(err.Error(), "escapes") {
		t.Fatalf("got %v, want an 'escapes' error", err)
	}
}

func TestResolveDocumentRejectsDuplicateBaseOwnership(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "a.yml", "foo:\n  bar: 1\n")
	writeFile(t, dir, "b.yml", "foo:\n  baz: 2\n")
	writeFile(t, dir, "apexvoid.yml", "version: 3\nincludes:\n  - a.yml\n  - b.yml\n")
	_, err := config.ResolveDocument(filepath.Join(dir, "apexvoid.yml"))
	if err == nil || !strings.Contains(err.Error(), "duplicate base ownership") {
		t.Fatalf("got %v, want a 'duplicate base ownership' error", err)
	}
}

func TestResolveDocumentOverlayReplacesScalarAndExtendsMap(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "a.yml", "foo:\n  bar: 1\n  baz: 2\n")
	if err := os.Mkdir(filepath.Join(dir, "environments"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(dir, "environments"), "test.yml", "foo:\n  bar: 99\n")
	writeFile(t, dir, "apexvoid.yml", "version: 3\nincludes:\n  - a.yml\n  - environments/test.yml\n")

	doc, err := config.ResolveDocument(filepath.Join(dir, "apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}
	bar, _ := doc.Get("foo.bar")
	if bar != 99 {
		t.Errorf("foo.bar = %v, want 99 (overlay replaces scalar)", bar)
	}
	baz, _ := doc.Get("foo.baz")
	if baz != 2 {
		t.Errorf("foo.baz = %v, want 2 (untouched sibling survives merge)", baz)
	}
	env, _ := doc.Get("runtime.environment")
	if env != "test" {
		t.Errorf("runtime.environment = %v, want \"test\"", env)
	}
}

func TestSectionFailsClosedOnMissingOrWrongType(t *testing.T) {
	// Built through the real ResolveDocument entry point rather than a
	// struct literal: from outside internal/config (this is now a
	// black-box test), Document's fields aren't reachable, and
	// constructing the document via a tiny synthetic root file — the same
	// pattern every other synthetic-fixture test on this page already
	// uses — is the correct black-box way to get a Document with a known
	// shape to assert on.
	dir := t.TempDir()
	writeFile(t, dir, "a.yml", "scalar: 1\n")
	writeFile(t, dir, "apexvoid.yml", "version: 3\nincludes:\n  - a.yml\n")
	doc, err := config.ResolveDocument(filepath.Join(dir, "apexvoid.yml"))
	if err != nil {
		t.Fatalf("ResolveDocument: %v", err)
	}
	if _, err := doc.Section("does.not.exist"); err == nil {
		t.Error("expected an error for a missing section")
	}
	if _, err := doc.Section("scalar"); err == nil {
		t.Error("expected an error for a non-mapping section")
	}
}
