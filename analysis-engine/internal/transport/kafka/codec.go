package kafka

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
)

// DecodeStrict unmarshals data into v, rejecting unknown JSON fields
// (source task §34: "reject unknown fields for V1 contract decoding
// during this controlled internal system, because it surfaces
// producer/consumer drift early") and trailing data after the JSON
// value. Used for both the envelope and every typed payload — a
// consumer never silently ignores a field a producer added that it
// doesn't know about.
func DecodeStrict(data []byte, v any) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return fmt.Errorf("kafka: decode: %w", err)
	}
	if _, err := dec.Token(); err != io.EOF {
		return fmt.Errorf("kafka: trailing data after JSON value")
	}
	return nil
}

// Encode marshals v — always a typed struct (source task §33: never
// map[string]any for a production event payload), so Go's own fixed
// struct-field encoding order already makes this deterministic without
// any extra canonicalization step.
func Encode(v any) ([]byte, error) {
	b, err := json.Marshal(v)
	if err != nil {
		return nil, fmt.Errorf("kafka: encode: %w", err)
	}
	return b, nil
}
