package config

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// GeometryFor builds a market.Geometry for symbol directly from the
// resolved V3 document's instruments.yml content — instruments.instruments.
// <symbol>, with instrument_packs.<pack> merged underneath it when the
// instrument declares one (the instrument's own leaves always win on a
// shared key, exactly matching instrument_packs.py's own merge rule and
// instruments.yml's own extensive comments on it). Fails closed for an
// unknown symbol or missing/invalid geometry — §12: no hardcoded pip size,
// ever, for any instrument.
func (d *Document) GeometryFor(symbol string) (market.Geometry, error) {
	instruments, err := d.Section("instruments")
	if err != nil {
		return market.Geometry{}, err
	}
	instrument, ok := instruments[symbol].(stringMap)
	if !ok {
		return market.Geometry{}, fmt.Errorf("config: unknown instrument %q", symbol)
	}

	merged := instrument
	if packName, ok := instrument["pack"].(string); ok && packName != "" {
		packs, err := d.Section("instrument_packs")
		if err != nil {
			return market.Geometry{}, err
		}
		pack, ok := packs[packName].(stringMap)
		if !ok {
			return market.Geometry{}, fmt.Errorf("config: instrument %q references unknown pack %q", symbol, packName)
		}
		merged = deepMerge(pack, instrument).(stringMap)
	}

	contract, ok := merged["contract"].(stringMap)
	if !ok {
		return market.Geometry{}, fmt.Errorf("config: instrument %q has no contract geometry (from instrument or pack)", symbol)
	}
	pipSize, err := numberField(contract, "pip_size")
	if err != nil {
		return market.Geometry{}, fmt.Errorf("config: instrument %q: %w", symbol, err)
	}
	digits, err := numberField(contract, "price_digits")
	if err != nil {
		return market.Geometry{}, fmt.Errorf("config: instrument %q: %w", symbol, err)
	}

	canonicalSymbol, _ := merged["canonical_symbol"].(string)
	if canonicalSymbol == "" {
		canonicalSymbol = symbol
	}
	brokerSymbol, _ := merged["broker_symbol"].(string)

	return market.NewGeometry(canonicalSymbol, brokerSymbol, pipSize, int(digits))
}

// LiveInstruments returns every instrument declared with rollout: live —
// the single source Configuration V3 wants for "which symbols are live"
// (rebuild-configuration-architecture.md §3), never a second, separately
// maintained list.
func (d *Document) LiveInstruments() ([]string, error) {
	instruments, err := d.Section("instruments")
	if err != nil {
		return nil, err
	}
	var live []string
	for symbol, raw := range instruments {
		instrument, ok := raw.(stringMap)
		if !ok {
			continue
		}
		if rollout, _ := instrument["rollout"].(string); rollout == "live" {
			live = append(live, symbol)
		}
	}
	return live, nil
}

// numberField reads a required numeric leaf as float64, regardless of
// whether yaml.v3 decoded it as int or float64 (YAML "0.1" decodes to
// float64; a bare "2" decodes to int) — a missing or non-numeric field is
// an error, never a silent zero (§9).
func numberField(m stringMap, key string) (float64, error) {
	value, ok := m[key]
	if !ok {
		return 0, fmt.Errorf("missing required field %q", key)
	}
	switch v := value.(type) {
	case float64:
		return v, nil
	case int:
		return float64(v), nil
	default:
		return 0, fmt.Errorf("field %q is not numeric (got %T)", key, value)
	}
}
