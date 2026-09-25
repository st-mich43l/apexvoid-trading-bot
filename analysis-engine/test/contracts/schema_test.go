// Package contracts_test proves the Go event DTOs
// (internal/transport/kafka) and the shared JSON schemas under
// contracts/ have not independently drifted (source task §59) — a
// schema and its Go mirror are maintained by hand (ADR-007's
// discipline; no generator exists yet), so this is the check that
// catches a hand-edit to one side without the other. The validator
// (github.com/santhosh-tekuri/jsonschema/v5) is used ONLY here, never
// from production code (ADR-008's "keep it out of the hot path unless
// runtime validation is deliberately desired").
package contracts_test

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v5"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func repoContractPath(parts ...string) string {
	return filepath.Join(append([]string{"..", "..", "..", "contracts"}, parts...)...)
}

func compileSchema(t *testing.T, path string) *jsonschema.Schema {
	t.Helper()
	compiler := jsonschema.NewCompiler()
	schema, err := compiler.Compile(path)
	if err != nil {
		t.Fatalf("compiling schema %s: %v", path, err)
	}
	return schema
}

// validateGo encodes v (a real Go DTO) and validates the result against
// schema — the round trip that actually proves Go and the schema agree,
// not just that the schema is itself well-formed.
func validateGo(t *testing.T, schema *jsonschema.Schema, v any) {
	t.Helper()
	encoded, err := kafka.Encode(v)
	if err != nil {
		t.Fatalf("encoding %#v: %v", v, err)
	}
	var generic any
	if err := json.Unmarshal(encoded, &generic); err != nil {
		t.Fatalf("re-decoding encoded JSON: %v", err)
	}
	if err := schema.Validate(generic); err != nil {
		t.Errorf("schema validation failed for %s:\n  encoded: %s\n  error: %v", filepath.Base(schema.Location), encoded, err)
	}
}

func TestEventEnvelopeSchema_ValidatesARealGoEnvelope(t *testing.T) {
	schema := compileSchema(t, repoContractPath("common", "event-envelope-v1.schema.json"))
	env := kafka.Envelope{
		EventID: kafka.NewEventID(), EventType: "analysis.opportunity.v1", EventVersion: 1,
		OccurredAt: 1_700_000_300, ProducedAt: 1_700_000_301, Producer: "analysis-engine",
		CorrelationID: "corr-1", CausationID: "cause-1",
		ConfigVersion: 3, ConfigFingerprint: "abc123",
		Payload: []byte(`{"a":1}`),
	}
	validateGo(t, schema, env)
}

func TestEventEnvelopeSchema_RejectsAnEnvelopeMissingARequiredField(t *testing.T) {
	schema := compileSchema(t, repoContractPath("common", "event-envelope-v1.schema.json"))
	raw := map[string]any{
		"event_type": "analysis.opportunity.v1", "event_version": 1,
		"occurred_at": 1, "produced_at": 1, "producer": "x", "correlation_id": "y",
		"payload": map[string]any{},
		// event_id deliberately omitted
	}
	if err := schema.Validate(raw); err == nil {
		t.Error("expected schema validation to fail for an envelope missing event_id")
	}
}

func TestOpportunitySchema_ValidatesARealGoPayload(t *testing.T) {
	schema := compileSchema(t, repoContractPath("analysis", "opportunity-v1.schema.json"))
	p := kafka.OpportunityPayload{
		ID: "cand-1", Strategy: "breakoutretest", Symbol: "XAU", Direction: "BUY",
		Entry:            kafka.EntryZonePayload{Low: 2000, High: 2002},
		Invalidation:     kafka.PriceLevelPayload{Price: 1990, Label: "structural_low"},
		Targets:          []kafka.TargetPayload{{Price: kafka.PriceLevelPayload{Price: 2020}}},
		Evidence:         []kafka.EvidencePayload{{Code: "m5_bos_up"}},
		Quality:          kafka.QualityPayload{Overall: 0.8, Components: map[string]float64{"breakout_quality": 0.9}},
		AlgorithmVersion: kafka.AlgorithmVersionPayload{Structure: "v2", Liquidity: "v1"},
		CreatedAt:        1000, ExpiresAt: 2000,
	}
	validateGo(t, schema, p)
}

func TestOpportunityInvalidatedSchema_ValidatesARealGoPayload(t *testing.T) {
	schema := compileSchema(t, repoContractPath("analysis", "opportunity-invalidated-v1.schema.json"))
	p := kafka.OpportunityInvalidatedPayload{
		OpportunityID: "cand-1", Symbol: "XAU", Strategy: "breakoutretest", ReasonCode: "STRUCTURE_INVALIDATED", InvalidatedAt: 1500,
	}
	validateGo(t, schema, p)
}
