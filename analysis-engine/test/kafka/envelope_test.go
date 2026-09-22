// Package kafka_test holds analysis-engine's Kafka transport tests,
// centralized per ADR-006 — never analysis-engine/internal/transport/kafka/*_test.go.
package kafka_test

import (
	"strings"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func TestNewEventID_IsAWellFormedUUIDv7(t *testing.T) {
	id := kafka.NewEventID()
	parts := strings.Split(id, "-")
	if len(parts) != 5 {
		t.Fatalf("expected 5 hyphen-separated groups, got %d: %q", len(parts), id)
	}
	lens := []int{8, 4, 4, 4, 12}
	for i, p := range parts {
		if len(p) != lens[i] {
			t.Errorf("group %d: expected length %d, got %d (%q)", i, lens[i], len(p), p)
		}
	}
	// version 7 nibble is the first hex digit of the 3rd group.
	if parts[2][0] != '7' {
		t.Errorf("expected version nibble '7', got %q in %q", parts[2][0:1], id)
	}
	// RFC 4122 variant: first hex digit of the 4th group must be 8/9/a/b.
	variant := parts[3][0]
	if variant != '8' && variant != '9' && variant != 'a' && variant != 'b' {
		t.Errorf("expected RFC 4122 variant nibble (8/9/a/b), got %q in %q", string(variant), id)
	}
}

func TestNewEventID_IsTimeSortableAndUnique(t *testing.T) {
	first := kafka.NewEventID()
	second := kafka.NewEventID()
	if first == second {
		t.Fatal("two calls to NewEventID produced the same value")
	}

	// UUIDv7's first 48 bits are a millisecond timestamp, so two IDs
	// minted in DIFFERENT milliseconds sort lexicographically by time —
	// the whole point of choosing v7 over v4 (source task §10). This
	// implementation does not add a monotonic counter for same-
	// millisecond ordering (RFC 9562 does not require one, and this
	// package's own docs already say event_id must never be relied on
	// for ordering by a consumer — Kafka partition ordering is
	// authoritative), so two IDs minted within the same millisecond may
	// legitimately sort either way; sleep past a millisecond boundary to
	// test the property this type actually guarantees.
	time.Sleep(2 * time.Millisecond)
	third := kafka.NewEventID()
	if third < second {
		t.Errorf("expected an ID minted at least 1ms later to sort at or after the earlier one: earlier=%s later=%s", second, third)
	}
}

func validEnvelope() kafka.Envelope {
	return kafka.Envelope{
		EventID: kafka.NewEventID(), EventType: "market.bar.closed.v1", EventVersion: 1,
		OccurredAt: 1000, ProducedAt: 1001, Producer: "ctrader-engine",
		CorrelationID: "corr-1", Payload: []byte(`{"a":1}`),
	}
}

func TestEnvelope_Validate_AcceptsAWellFormedEnvelope(t *testing.T) {
	if err := validEnvelope().Validate(); err != nil {
		t.Fatalf("expected a valid envelope to pass, got: %v", err)
	}
}

func TestEnvelope_Validate_RejectsEachMissingRequiredField(t *testing.T) {
	cases := map[string]func(*kafka.Envelope){
		"event_id":       func(e *kafka.Envelope) { e.EventID = "" },
		"event_type":     func(e *kafka.Envelope) { e.EventType = "" },
		"event_version":  func(e *kafka.Envelope) { e.EventVersion = 0 },
		"occurred_at":    func(e *kafka.Envelope) { e.OccurredAt = -1 },
		"produced_at":    func(e *kafka.Envelope) { e.ProducedAt = -1 },
		"producer":       func(e *kafka.Envelope) { e.Producer = "" },
		"correlation_id": func(e *kafka.Envelope) { e.CorrelationID = "" },
		"payload":        func(e *kafka.Envelope) { e.Payload = nil },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			env := validEnvelope()
			mutate(&env)
			err := env.Validate()
			if err == nil {
				t.Fatalf("expected an error when %s is missing/invalid", name)
			}
			if !kafka.IsPermanent(err) {
				t.Errorf("expected a malformed envelope to be classified Permanent, got a non-permanent error: %v", err)
			}
		})
	}
}

func TestEnvelope_Validate_CausationIDAndConfigProvenanceAreOptional(t *testing.T) {
	env := validEnvelope() // CausationID/ConfigVersion/ConfigFingerprint left at zero value
	if err := env.Validate(); err != nil {
		t.Fatalf("a root event with no causation_id and no config provenance must still validate, got: %v", err)
	}
}
