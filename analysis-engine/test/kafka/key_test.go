package kafka_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func TestRecordKey_UsesCanonicalSymbolForOpportunityLifecycle(t *testing.T) {
	if got := string(kafka.RecordKey("XAU")); got != "XAU" {
		t.Errorf("expected the record key to be exactly the canonical symbol, got %q", got)
	}
}

func TestRecordKey_DifferentSymbolsProduceDifferentKeys(t *testing.T) {
	xau := kafka.RecordKey("XAU")
	eurusd := kafka.RecordKey("EURUSD")
	if string(xau) == string(eurusd) {
		t.Error("expected different symbols to produce different record keys")
	}
}
