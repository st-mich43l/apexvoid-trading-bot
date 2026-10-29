package config

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// GeometryFor builds a market.Geometry for symbol directly from the
// resolved native document's instruments.yml content — instruments.instruments.
// <symbol>, with instrument_packs.<pack> merged underneath it when the
// instrument declares one (the instrument's own leaves always win on a
// shared key, exactly matching instrument_packs.py's own merge rule and
// instruments.yml's own extensive comments on it). Fails closed for an
// unknown symbol or missing/invalid geometry — §12: no hardcoded pip size,
// ever, for any instrument.
func (d *Document) GeometryFor(symbol string) (market.Geometry, error) {
	merged, err := d.mergedInstrument(symbol)
	if err != nil {
		return market.Geometry{}, err
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
// the single source configuration uses for "which symbols are live"
// (docs/configuration.md), never a second, separately
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

// mergedInstrument returns instruments.<symbol> with its instrument pack
// merged underneath (the instrument's own leaves always win).
func (d *Document) mergedInstrument(symbol string) (stringMap, error) {
	instruments, err := d.Section("instruments")
	if err != nil {
		return nil, err
	}
	instrument, ok := instruments[symbol].(stringMap)
	if !ok {
		return nil, fmt.Errorf("config: unknown instrument %q", symbol)
	}
	merged := instrument
	if packName, ok := instrument["pack"].(string); ok && packName != "" {
		packs, err := d.Section("instrument_packs")
		if err != nil {
			return nil, err
		}
		pack, ok := packs[packName].(stringMap)
		if !ok {
			return nil, fmt.Errorf("config: instrument %q references unknown pack %q", symbol, packName)
		}
		merged = deepMerge(pack, instrument).(stringMap)
	}
	return merged, nil
}

// DefendedLevelsFor returns the prices an instrument's authorities defend
// (a market-intervention ceiling such as USDJPY 160) and the buffer around
// them, from overrides.auto_algo.risk.exposure of the merged instrument.
// An instrument that declares none returns no levels and no error.
func (d *Document) DefendedLevelsFor(symbol string) ([]float64, float64, error) {
	merged, err := d.mergedInstrument(symbol)
	if err != nil {
		return nil, 0, err
	}
	exposure := nestedMap(merged, "overrides", "auto_algo", "risk", "exposure")
	if exposure == nil {
		return nil, 0, nil
	}
	rawLevels, present := exposure["defended_levels"]
	if !present {
		return nil, 0, nil
	}
	list, ok := rawLevels.([]any)
	if !ok {
		return nil, 0, fmt.Errorf("config: instrument %q defended_levels must be a list of prices", symbol)
	}
	levels := make([]float64, 0, len(list))
	for _, item := range list {
		switch v := item.(type) {
		case float64:
			levels = append(levels, v)
		case int:
			levels = append(levels, float64(v))
		default:
			return nil, 0, fmt.Errorf("config: instrument %q defended_levels entry %v is not numeric", symbol, item)
		}
	}
	if len(levels) == 0 {
		return nil, 0, nil
	}
	buffer, err := numberField(exposure, "defended_level_buffer_price")
	if err != nil {
		return nil, 0, fmt.Errorf("config: instrument %q: %w", symbol, err)
	}
	if !(buffer > 0) {
		return nil, 0, fmt.Errorf("config: instrument %q defended_level_buffer_price must be positive", symbol)
	}
	return levels, buffer, nil
}

func nestedMap(m stringMap, path ...string) stringMap {
	cursor := m
	for _, key := range path {
		next, ok := cursor[key].(stringMap)
		if !ok {
			return nil
		}
		cursor = next
	}
	return cursor
}

// InstrumentOverride reads one leaf of an instrument's merged overrides tree,
// e.g. ("GBPJPY", "analysis", "levels", "minimum_key_touches"). It reports
// whether the instrument (or its pack) sets it; an unknown instrument is an
// error. This is the same Python-shaped override tree the algo-bot resolves, so
// a per-instrument value has one home for both services.
func (d *Document) InstrumentOverride(symbol string, path ...string) (any, bool, error) {
	merged, err := d.mergedInstrument(symbol)
	if err != nil {
		return nil, false, err
	}
	cursor := nestedMap(merged, "overrides")
	if cursor == nil || len(path) == 0 {
		return nil, false, nil
	}
	for _, key := range path[:len(path)-1] {
		next, ok := cursor[key].(stringMap)
		if !ok {
			return nil, false, nil
		}
		cursor = next
	}
	value, ok := cursor[path[len(path)-1]]
	return value, ok, nil
}

// InstrumentValue reads one leaf of an instrument's merged declaration (pack
// underneath, instrument on top), e.g. ("XAU", "price_scale",
// "fvg_entry_max_width_price"). It reports whether it is set.
func (d *Document) InstrumentValue(symbol string, path ...string) (any, bool, error) {
	merged, err := d.mergedInstrument(symbol)
	if err != nil {
		return nil, false, err
	}
	var cursor any = merged
	for _, key := range path {
		m, ok := cursor.(stringMap)
		if !ok {
			return nil, false, nil
		}
		cursor, ok = m[key]
		if !ok {
			return nil, false, nil
		}
	}
	return cursor, true, nil
}

// StopEnvelopeFor returns the resolved instrument stop-distance envelope.
// The envelope is composed from the instrument pack and the concrete
// instrument override, exactly like GeometryFor and InstrumentValue.  It is
// deliberately read from instruments.yml rather than inferred from the
// symbol or copied from execution defaults: live FX pairs have materially
// different envelopes (for example EURUSD 12-20 and GBPJPY 22-35 pips).
func (d *Document) StopEnvelopeFor(symbol string) (minPips, maxPips float64, err error) {
	merged, err := d.mergedInstrument(symbol)
	if err != nil {
		return 0, 0, err
	}
	envelope, ok := merged["stop_envelope"].(stringMap)
	if !ok {
		return 0, 0, fmt.Errorf("config: instrument %q has no stop_envelope", symbol)
	}
	minPips, err = numberField(envelope, "min_pips")
	if err != nil {
		return 0, 0, fmt.Errorf("config: instrument %q stop_envelope: %w", symbol, err)
	}
	maxPips, err = numberField(envelope, "max_pips")
	if err != nil {
		return 0, 0, fmt.Errorf("config: instrument %q stop_envelope: %w", symbol, err)
	}
	if !(minPips > 0) || !(maxPips >= minPips) {
		return 0, 0, fmt.Errorf("config: instrument %q stop_envelope must satisfy 0 < min_pips <= max_pips, got %.3f-%.3f", symbol, minPips, maxPips)
	}
	return minPips, maxPips, nil
}
