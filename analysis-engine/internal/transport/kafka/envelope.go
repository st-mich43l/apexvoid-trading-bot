package kafka

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"time"
)

// Envelope is the shared cross-service event wrapper
// (contracts/common/event-envelope-v1.schema.json) — kept in sync with
// that schema by hand (ADR-007's discipline; no schema-to-struct
// generator exists yet). Every field's meaning and timestamp unit is
// documented once, on the schema file itself; this struct's job is to
// be the exact typed mirror of it.
type Envelope struct {
	EventID      string `json:"event_id"`
	EventType    string `json:"event_type"`
	EventVersion int    `json:"event_version"`

	OccurredAt int64 `json:"occurred_at"` // Unix seconds — docs/transport/kafka.md's "Timestamp standard"
	ProducedAt int64 `json:"produced_at"` // Unix seconds

	Producer string `json:"producer"`

	CorrelationID string `json:"correlation_id"`
	CausationID   string `json:"causation_id,omitempty"`

	ConfigVersion     int    `json:"config_version,omitempty"`
	ConfigFingerprint string `json:"config_fingerprint,omitempty"`

	Payload json.RawMessage `json:"payload"`
}

// Validate checks the envelope's own required fields per
// contracts/common/event-envelope-v1.schema.json, independent of
// whatever event_type-specific payload it wraps. Every failure is
// Permanent — a malformed envelope can never become valid by retrying
// (source task §19).
func (e Envelope) Validate() error {
	switch {
	case e.EventID == "":
		return Permanent(fmt.Errorf("kafka: envelope event_id is required"))
	case e.EventType == "":
		return Permanent(fmt.Errorf("kafka: envelope event_type is required"))
	case e.EventVersion < 1:
		return Permanent(fmt.Errorf("kafka: envelope event_version must be >= 1, got %d", e.EventVersion))
	case e.OccurredAt < 0:
		return Permanent(fmt.Errorf("kafka: envelope occurred_at must be >= 0, got %d", e.OccurredAt))
	case e.ProducedAt < 0:
		return Permanent(fmt.Errorf("kafka: envelope produced_at must be >= 0, got %d", e.ProducedAt))
	case e.Producer == "":
		return Permanent(fmt.Errorf("kafka: envelope producer is required"))
	case e.CorrelationID == "":
		return Permanent(fmt.Errorf("kafka: envelope correlation_id is required"))
	case len(e.Payload) == 0:
		return Permanent(fmt.Errorf("kafka: envelope payload is required"))
	}
	return nil
}

// NewEventID returns a fresh, time-sortable UUIDv7 (source task §10,
// RFC 9562 §5.7) — hand-rolled rather than adding a UUID dependency:
// this module's established preference (one dependency, yaml.v3, before
// this task added franz-go for real transport weight) is to reach for a
// library only when it earns its place; a 16-byte layout with a handful
// of bit-twiddles does not. Never used for ordering or deduplication by
// a consumer (source task §10 explicitly) — Kafka partition ordering and
// the payload's own domain identity are authoritative for those.
func NewEventID() string {
	var b [16]byte
	ms := uint64(time.Now().UnixMilli())
	b[0] = byte(ms >> 40)
	b[1] = byte(ms >> 32)
	b[2] = byte(ms >> 24)
	b[3] = byte(ms >> 16)
	b[4] = byte(ms >> 8)
	b[5] = byte(ms)
	if _, err := rand.Read(b[6:]); err != nil {
		// crypto/rand failing is effectively unrecoverable on any real
		// platform this runs on; a deterministic-but-still-varying
		// fallback keeps event ID generation from ever panicking
		// mid-publish rather than trying to be cryptographically sound
		// in an already-degenerate situation.
		fallbackRandom(b[6:])
	}
	b[6] = (b[6] & 0x0F) | 0x70 // version 7
	b[8] = (b[8] & 0x3F) | 0x80 // RFC 4122 variant
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

func fallbackRandom(b []byte) {
	seed := uint64(time.Now().UnixNano())
	for i := range b {
		seed = seed*6364136223846793005 + 1442695040888963407
		b[i] = byte(seed >> 56)
	}
}
