package config

import (
	"path/filepath"
	"testing"
)

// fixturePath is a copy of contracts/configuration/runtime-manifest-
// example.generated.json — the same generated manifest example Python and
// .NET tests already treat as the reference shape (§13: one manifest, one
// authority). Kept inside this module's testdata so `go test ./...` never
// reaches outside analysis-engine/.
func fixturePath() string {
	return filepath.Join("..", "..", "testdata", "runtime-manifest-example.json")
}

func TestLoadParsesRealManifestExample(t *testing.T) {
	m, err := Load(fixturePath())
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if m.ManifestVersion != 2 {
		t.Errorf("ManifestVersion = %d, want 2", m.ManifestVersion)
	}
	if m.Profile != "conservative" {
		t.Errorf("Profile = %q, want %q", m.Profile, "conservative")
	}
	wantLive := []string{"EURUSD", "GBPJPY", "GBPUSD", "USDJPY", "XAU"}
	if len(m.LiveInstruments) != len(wantLive) {
		t.Fatalf("LiveInstruments = %v, want %v", m.LiveInstruments, wantLive)
	}
	for i, sym := range wantLive {
		if m.LiveInstruments[i] != sym {
			t.Errorf("LiveInstruments[%d] = %q, want %q", i, m.LiveInstruments[i], sym)
		}
	}
}

func TestLoadUnknownFileFailsClosed(t *testing.T) {
	if _, err := Load(filepath.Join("..", "..", "testdata", "does-not-exist.json")); err == nil {
		t.Fatal("expected an error for a missing manifest file")
	}
}

func TestInstrumentUnknownSymbolFailsClosed(t *testing.T) {
	m, err := Load(fixturePath())
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if _, err := m.Instrument("DOGEUSD"); err == nil {
		t.Fatal("expected an error for an unrecognized instrument")
	}
}

func TestGeometryForXAUMatchesManifest(t *testing.T) {
	m, err := Load(fixturePath())
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	g, err := m.GeometryFor("XAU")
	if err != nil {
		t.Fatalf("GeometryFor(XAU): %v", err)
	}
	if g.Symbol != "XAU" {
		t.Errorf("Symbol = %q, want %q", g.Symbol, "XAU")
	}
	if g.PriceDigits != 2 {
		t.Errorf("PriceDigits = %d, want 2", g.PriceDigits)
	}
	if diff := g.PipSize - 0.1; diff > 1e-9 || diff < -1e-9 {
		t.Errorf("PipSize = %v, want 0.1", g.PipSize)
	}
}

func TestGeometryForEveryLiveInstrumentSucceeds(t *testing.T) {
	// §12: unknown instrument geometry must fail closed — the inverse
	// check matters too: every symbol the manifest actually declares live
	// must resolve without error, or a real instrument would silently be
	// unusable in Go.
	m, err := Load(fixturePath())
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	for _, sym := range m.LiveInstruments {
		if _, err := m.GeometryFor(sym); err != nil {
			t.Errorf("GeometryFor(%s): %v", sym, err)
		}
	}
}

func TestDecimalUnmarshalsStringAndNumber(t *testing.T) {
	var d Decimal
	if err := d.UnmarshalJSON([]byte(`"0.1"`)); err != nil {
		t.Fatalf("UnmarshalJSON(string): %v", err)
	}
	if d != 0.1 {
		t.Errorf("got %v, want 0.1", d)
	}
	if err := d.UnmarshalJSON([]byte(`2`)); err != nil {
		t.Fatalf("UnmarshalJSON(number): %v", err)
	}
	if d != 2 {
		t.Errorf("got %v, want 2", d)
	}
	if err := d.UnmarshalJSON([]byte(`"not-a-number"`)); err == nil {
		t.Fatal("expected an error for a non-numeric string")
	}
}
