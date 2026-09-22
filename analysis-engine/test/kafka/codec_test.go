package kafka_test

import (
	"encoding/json"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func validBarPayload() kafka.BarClosedPayload {
	return kafka.BarClosedPayload{
		CanonicalSymbol: "XAU", Timeframe: "M5",
		OpenTime: 1_700_000_000, CloseTime: 1_700_000_300,
		Open: 2000, High: 2005, Low: 1998, Close: 2003, Volume: 120,
	}
}

func TestDecodeStrict_RoundTripsAValidBarPayload(t *testing.T) {
	want := validBarPayload()
	encoded, err := kafka.Encode(want)
	if err != nil {
		t.Fatalf("Encode failed: %v", err)
	}
	var got kafka.BarClosedPayload
	if err := kafka.DecodeStrict(encoded, &got); err != nil {
		t.Fatalf("DecodeStrict failed: %v", err)
	}
	if got != want {
		t.Errorf("round-trip mismatch:\n got=%+v\nwant=%+v", got, want)
	}
}

func TestDecodeStrict_RejectsAnUnknownField(t *testing.T) {
	raw := `{"canonical_symbol":"XAU","timeframe":"M5","open_time":1,"close_time":301,"open":1,"high":1,"low":1,"close":1,"volume":1,"unexpected_field":"drift"}`
	var got kafka.BarClosedPayload
	err := kafka.DecodeStrict([]byte(raw), &got)
	if err == nil {
		t.Fatal("expected DecodeStrict to reject an unknown field (source task §34), got nil error")
	}
}

func TestDecodeStrict_RejectsTrailingDataAfterTheJSONValue(t *testing.T) {
	raw := `{"canonical_symbol":"XAU","timeframe":"M5","open_time":1,"close_time":301,"open":1,"high":1,"low":1,"close":1,"volume":1} {"a":1}`
	var got kafka.BarClosedPayload
	if err := kafka.DecodeStrict([]byte(raw), &got); err == nil {
		t.Fatal("expected DecodeStrict to reject trailing data after the JSON value, got nil error")
	}
}

func TestDecodeStrict_RejectsMalformedJSON(t *testing.T) {
	var got kafka.BarClosedPayload
	if err := kafka.DecodeStrict([]byte(`{not json`), &got); err == nil {
		t.Fatal("expected DecodeStrict to reject malformed JSON, got nil error")
	}
}

func TestDecodeStrict_RejectsMissingRequiredField(t *testing.T) {
	// "timeframe" omitted entirely — DecodeStrict itself does not
	// enforce required-ness (that's Go zero-value + this package's own
	// semantic Validate/adapter checks), but the decode must still
	// succeed structurally so the semantic layer can produce a precise
	// error; this test documents that boundary rather than conflating
	// the two layers.
	raw := `{"canonical_symbol":"XAU","open_time":1,"close_time":301,"open":1,"high":1,"low":1,"close":1,"volume":1}`
	var got kafka.BarClosedPayload
	if err := kafka.DecodeStrict([]byte(raw), &got); err != nil {
		t.Fatalf("structural decode of a payload with a merely-absent field should succeed, got: %v", err)
	}
	if got.Timeframe != "" {
		t.Fatalf("expected zero-value Timeframe, got %q", got.Timeframe)
	}
	if _, err := kafka.BarEventFromPayload(got); err == nil {
		t.Fatal("expected BarEventFromPayload to reject a payload with an empty timeframe")
	}
}

func TestEncode_ProducesTheExpectedJSONFieldNames(t *testing.T) {
	b, err := kafka.Encode(validBarPayload())
	if err != nil {
		t.Fatalf("Encode failed: %v", err)
	}
	var generic map[string]any
	if err := json.Unmarshal(b, &generic); err != nil {
		t.Fatalf("Encode did not produce valid JSON: %v", err)
	}
	for _, field := range []string{"canonical_symbol", "timeframe", "open_time", "close_time", "open", "high", "low", "close", "volume"} {
		if _, ok := generic[field]; !ok {
			t.Errorf("expected encoded JSON to contain field %q, got keys %v", field, keysOf(generic))
		}
	}
}

func keysOf(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
