// Package config loads the shared ResolvedRuntimeManifest — the same file
// Python (app/configuration/runtime_manifest.py) and the .NET cTrader
// engine (ResolvedRuntimeManifestLoader.cs) already treat as the single
// configuration authority. This package must never grow a second
// precedence engine, YAML parser, or ad-hoc ENV reader for analysis
// behavior — see rebuild-analysis-engine.md §13.
package config

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
)

// ManifestFileEnv is the environment variable Python's own loader
// (app/configuration/runtime_manifest_boot.py::MANIFEST_FILE_ENV) reads to
// find the compiled manifest file. Kept as one named constant, not a
// string literal, so the three runtimes can never quietly drift apart on
// the variable name.
const ManifestFileEnv = "APEXVOID_RUNTIME_MANIFEST_FILE"

// Decimal unmarshals a manifest field that the compiler emits as a JSON
// string (to preserve exact decimal precision across Python's Decimal /
// .NET's decimal — see the manifest's own `pip_size: "0.1"` style fields)
// into a float64. It also accepts a plain JSON number, since some fields
// (price_digits, volume_units_per_lot) are emitted unquoted — see
// contracts/configuration/runtime-manifest-example.generated.json.
type Decimal float64

func (d *Decimal) UnmarshalJSON(data []byte) error {
	var raw any
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	switch v := raw.(type) {
	case string:
		f, err := strconv.ParseFloat(v, 64)
		if err != nil {
			return fmt.Errorf("config: decimal field %q is not a number: %w", v, err)
		}
		*d = Decimal(f)
	case float64:
		*d = Decimal(v)
	case nil:
		*d = 0
	default:
		return fmt.Errorf("config: decimal field has unsupported JSON type %T", raw)
	}
	return nil
}

// Units is the per-instrument contract/pip geometry block
// (`instrument_runtimes.<SYMBOL>.units` in the manifest). Field set matches
// what the manifest example currently emits; extend deliberately, not by
// guessing at names — see §12 (no hardcoded pip sizes; unknown geometry
// fails closed).
type Units struct {
	PipSize             Decimal `json:"pip_size"`
	PriceDigits         int     `json:"price_digits"`
	ContractUnitsPerLot Decimal `json:"contract_units_per_lot"`
	PipValuePerLot      Decimal `json:"pip_value_per_lot"`
	MaxLots             Decimal `json:"max_lots"`
	VolumeUnitsPerLot   int64   `json:"volume_units_per_lot"`
}

// Identity is the per-instrument identity block.
type Identity struct {
	InstrumentID    string   `json:"instrument_id"`
	BrokerSymbol    string   `json:"broker_symbol"`
	CanonicalSymbol string   `json:"canonical_symbol"`
	Aliases         []string `json:"aliases"`
	Rollout         string   `json:"rollout"`
	Timeframes      []string `json:"timeframes"`
}

// InstrumentRuntime is one entry of `instrument_runtimes` in the manifest.
// Only the fields this migration slice needs are typed out; the rest of
// the (very large) per-instrument tree is preserved via RawAnalysis for
// later stages to type as they're actually ported, rather than guessed at
// now (§17 — don't invent structure ahead of reading the Python that owns
// it).
type InstrumentRuntime struct {
	Identity Identity        `json:"identity"`
	Units    Units           `json:"units"`
	Analysis json.RawMessage `json:"analysis"`
}

// Manifest is the root ResolvedRuntimeManifest shape. Only the fields this
// migration slice needs are typed; unknown/future top-level keys are
// preserved in Raw for forward compatibility rather than rejected — the
// manifest is versioned (`manifest_version`) and owned by the compiler,
// not by this reader.
type Manifest struct {
	ManifestVersion            int                          `json:"manifest_version"`
	Profile                    string                       `json:"profile"`
	ContractFingerprint        string                       `json:"contract_fingerprint"`
	EffectiveConfigFingerprint string                       `json:"effective_configuration_fingerprint"`
	LiveInstruments            []string                     `json:"live_instruments"`
	InstrumentRuntimes         map[string]InstrumentRuntime `json:"instrument_runtimes"`
	Raw                        json.RawMessage              `json:"-"`
}

// Load reads and parses the manifest file at path. It does not fall back
// to any default file location or embedded config — an unset/unreadable
// manifest must fail the process closed (§12/§13), not run on guessed
// geometry.
func Load(path string) (*Manifest, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("config: reading manifest %q: %w", path, err)
	}
	var m Manifest
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, fmt.Errorf("config: parsing manifest %q: %w", path, err)
	}
	m.Raw = data
	return &m, nil
}

// LoadFromEnv reads ManifestFileEnv and loads the manifest it points to.
// Returns an error (never a default manifest) when the variable is unset —
// same fail-closed rule as Load.
func LoadFromEnv() (*Manifest, error) {
	path := os.Getenv(ManifestFileEnv)
	if path == "" {
		return nil, fmt.Errorf("config: %s is not set", ManifestFileEnv)
	}
	return Load(path)
}

// Instrument looks up one instrument's runtime block by its canonical
// symbol (e.g. "XAU"). Returns an error for an unrecognized symbol —
// callers must fail closed rather than fabricate geometry (§12).
func (m *Manifest) Instrument(symbol string) (InstrumentRuntime, error) {
	rt, ok := m.InstrumentRuntimes[symbol]
	if !ok {
		return InstrumentRuntime{}, fmt.Errorf("config: unknown instrument %q in manifest", symbol)
	}
	return rt, nil
}
