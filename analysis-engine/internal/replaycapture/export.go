package replaycapture

import (
	"encoding/json"
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// Producer is the name the Python consumer requires on every envelope.
const Producer = "apexvoid-analysis-engine"

// EnvelopeOptions carries the provenance stamped on every exported envelope.
type EnvelopeOptions struct {
	Algorithm         kafka.AlgorithmVersion
	ConfigVersion     int
	ConfigFingerprint string
}

// CreationEnvelope encodes one opportunity creation exactly as
// kafka.Producer.PublishOpportunity would (same payload adapter, same envelope
// shape), with replay-deterministic identifiers and times instead of a random
// event ID and the wall clock: event_id is derived from the opportunity ID,
// occurred_at and produced_at are the opportunity's own CreatedAt.
func CreationEnvelope(c opportunity.Candidate, opts EnvelopeOptions) ([]byte, error) {
	payload, err := kafka.Encode(kafka.OpportunityPayloadFromCandidate(c, opts.Algorithm))
	if err != nil {
		return nil, err
	}
	eventID := "replay-" + c.ID
	env := kafka.Envelope{
		EventID: eventID, EventType: "analysis.opportunity.v1", EventVersion: 1,
		OccurredAt: c.CreatedAt, ProducedAt: c.CreatedAt, Producer: Producer, CorrelationID: eventID,
		ConfigVersion: opts.ConfigVersion, ConfigFingerprint: opts.ConfigFingerprint, Payload: payload,
	}
	if err := env.Validate(); err != nil {
		return nil, fmt.Errorf("replaycapture: %w", err)
	}
	return json.Marshal(env)
}
