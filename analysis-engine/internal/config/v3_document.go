// Package config loads Configuration V3 (config/apexvoid.yml + its
// categorized includes + one environment overlay) directly — the same
// files algo-bot's app/configuration/v3_root.py reads, and the same
// include/merge/overlay spec (rebuild-configuration-architecture.md §14)
// that module and config/scripts/resolve_reference.py both implement.
// This package must never grow a second precedence engine, defaults
// system, or ad-hoc ENV reader for analysis behavior — see
// rebuild-analysis-engine.md §13 and rebuild-configuration-architecture.md
// §9 (Go: "config structs may use zero values only during
// deserialization, but validation must reject missing required config
// rather than treating zero as a default").
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// RootFileEnv is the one non-secret configuration bootstrap environment
// variable (rebuild-configuration-architecture.md §12), the same name
// Python's app/configuration/config_file.py (CONFIG_FILE_ENV) and this
// repo's docker-compose.yml already use.
const RootFileEnv = "APEXVOID_CONFIG_FILE"

// stringMap is the generic node shape a YAML mapping unmarshals to via
// yaml.v3 when the target type is `any` — every category file, and the
// resolved document as a whole, is walked as this shape rather than
// bound to per-category Go structs. Building full typed structs for
// every category (like the JSON Schema's singleton-category defs) is
// deferred until a consumer other than instrument geometry needs one;
// see this file's own doc comment on why un-typed here is the right
// scope for what analysis-engine reads today.
type stringMap = map[string]any

// Document is a resolved Configuration V3 document — every category's
// data, merged and overlaid, addressable by dotted path.
type Document struct {
	raw stringMap
}

// ResolveDocument reads a V3 root file (config/apexvoid.yml-shaped: a
// `version: 3` document with an `includes:` list) and resolves its
// include graph plus environment overlay, exactly mirroring
// app/configuration/v3_root.py::resolve_v3_document and
// config/scripts/resolve_reference.py's own reference implementation —
// three independent implementations of the identical §14 spec, which is
// the whole point (cross-language parity, §38).
func ResolveDocument(rootPath string) (*Document, error) {
	root, err := loadYAML(rootPath)
	if err != nil {
		return nil, err
	}
	rootMap, ok := root.(stringMap)
	if !ok {
		return nil, fmt.Errorf("config: %s: top-level V3 root document must be a mapping", rootPath)
	}
	if v, _ := rootMap["version"].(int); v != 3 {
		return nil, fmt.Errorf("config: %s: unsupported version %v — only 3 is supported (§18)", rootPath, rootMap["version"])
	}
	includesRaw, ok := rootMap["includes"].([]any)
	if !ok || len(includesRaw) == 0 {
		return nil, fmt.Errorf("config: %s: 'includes' must be a non-empty list", rootPath)
	}

	baseDir := filepath.Dir(rootPath)
	merged := stringMap{"version": 3}
	seen := map[string]bool{}
	var overlay stringMap
	var environment string

	for _, item := range includesRaw {
		include, ok := item.(string)
		if !ok || include == "" {
			return nil, fmt.Errorf("config: %s: invalid include entry %v", rootPath, item)
		}
		if seen[include] {
			return nil, fmt.Errorf("config: %s: duplicate include %q", rootPath, include)
		}
		seen[include] = true
		if strings.HasPrefix(include, "/") || strings.Contains(include, "..") {
			return nil, fmt.Errorf("config: %s: include %q escapes the config root — not allowed", rootPath, include)
		}
		includePath := filepath.Join(baseDir, include)
		if _, err := os.Stat(includePath); err != nil {
			return nil, fmt.Errorf("config: %s: missing include %q", rootPath, include)
		}

		doc, err := loadYAML(includePath)
		if err != nil {
			return nil, err
		}
		docMap, _ := doc.(stringMap)

		if strings.HasPrefix(include, "environments/") {
			if overlay != nil {
				return nil, fmt.Errorf("config: %s: more than one environments/*.yml include", rootPath)
			}
			overlay = docMap
			environment = strings.TrimSuffix(filepath.Base(include), ".yml")
			continue
		}

		for topKey, value := range docMap {
			if topKey == "version" {
				continue
			}
			if _, exists := merged[topKey]; exists {
				return nil, fmt.Errorf(
					"config: %s: duplicate base ownership of top-level key %q (already set before %q was included)",
					rootPath, topKey, include,
				)
			}
			merged[topKey] = value
		}
	}

	if overlay != nil {
		merged = deepMerge(merged, overlay).(stringMap)
	}
	runtimeSection, _ := merged["runtime"].(stringMap)
	if runtimeSection == nil {
		runtimeSection = stringMap{}
		merged["runtime"] = runtimeSection
	}
	if environment == "" {
		environment = "production"
	}
	runtimeSection["environment"] = environment

	return &Document{raw: merged}, nil
}

func loadYAML(path string) (any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("config: reading %s: %w", path, err)
	}
	var out any
	if err := yaml.Unmarshal(data, &out); err != nil {
		return nil, fmt.Errorf("config: parsing %s: %w", path, err)
	}
	return normalizeYAMLNode(out), nil
}

// normalizeYAMLNode converts yaml.v3's map[string]interface{} (which is
// what it actually produces for a mapping, unlike some YAML libraries
// that use map[interface{}]interface{}) recursively so every mapping in
// the tree is consistently stringMap, and every list is []any — this
// keeps deepMerge and every accessor in this package simple, with one
// normalization pass instead of type-switching everywhere else.
func normalizeYAMLNode(node any) any {
	switch v := node.(type) {
	case map[string]any:
		out := make(stringMap, len(v))
		for key, value := range v {
			out[key] = normalizeYAMLNode(value)
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, value := range v {
			out[i] = normalizeYAMLNode(value)
		}
		return out
	default:
		return v
	}
}

// deepMerge implements §14 exactly: mappings deep-merge; scalars and
// lists are replaced wholesale by the overlay, never concatenated.
func deepMerge(base, overlay any) any {
	baseMap, baseOK := base.(stringMap)
	overlayMap, overlayOK := overlay.(stringMap)
	if !baseOK || !overlayOK {
		return overlay
	}
	out := make(stringMap, len(baseMap))
	for k, v := range baseMap {
		out[k] = v
	}
	for k, overlayValue := range overlayMap {
		if baseValue, exists := out[k]; exists {
			if _, baseIsMap := baseValue.(stringMap); baseIsMap {
				if _, overlayIsMap := overlayValue.(stringMap); overlayIsMap {
					out[k] = deepMerge(baseValue, overlayValue)
					continue
				}
			}
		}
		out[k] = overlayValue
	}
	return out
}

// Get walks a dotted path (e.g. "instruments.instruments.XAU.contract.
// pip_size") through the resolved document and returns the value found
// there, or (nil, false) if any segment along the way doesn't exist or
// isn't a mapping.
func (d *Document) Get(dottedPath string) (any, bool) {
	var cursor any = d.raw
	for _, part := range strings.Split(dottedPath, ".") {
		m, ok := cursor.(stringMap)
		if !ok {
			return nil, false
		}
		cursor, ok = m[part]
		if !ok {
			return nil, false
		}
	}
	return cursor, true
}

// Raw returns the whole resolved document as an untyped tree
// (map[string]any/[]any/scalars, exactly what yaml.v3 produces). Exported
// only for the Stage C6 cross-language fixture-parity test
// (test/config/v3_fixture_parity_test.go), which needs the complete
// resolved shape to compare against resolve_reference.py's canonical
// fixture — not part of the normal consumer surface (use Get/Section for
// everything else).
func (d *Document) Raw() stringMap {
	return d.raw
}

// Section returns the mapping at dottedPath (e.g. "instruments"), or an
// error if it's missing or not a mapping — the Go analogue of §9's "a
// missing required config value fails, zero is never treated as a
// default."
func (d *Document) Section(dottedPath string) (stringMap, error) {
	value, ok := d.Get(dottedPath)
	if !ok {
		return nil, fmt.Errorf("config: missing required section %q", dottedPath)
	}
	m, ok := value.(stringMap)
	if !ok {
		return nil, fmt.Errorf("config: %q is not a mapping (got %T)", dottedPath, value)
	}
	return m, nil
}
