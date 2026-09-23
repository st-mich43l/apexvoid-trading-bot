// Kafka transport benchmarks: JSON envelope encode, JSON envelope decode,
// and opportunity encode. No
// network — these measure the codec/adapter, not broker throughput
// (§66: "broker throughput benchmarking can be a separate tool," not
// this centralized suite).
//
// Run: go test -bench=Kafka -benchmem ./test/benchmark/...
package benchmark_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func benchEnvelope() kafka.Envelope {
	payload, _ := kafka.Encode(kafka.OpportunityPayload{ID: "cand-1", Strategy: "breakoutretest", Symbol: "XAU", Direction: "BUY"})
	return kafka.Envelope{
		EventID: kafka.NewEventID(), EventType: "analysis.opportunity.v1", EventVersion: 1,
		OccurredAt: 1_700_000_300, ProducedAt: 1_700_000_301, Producer: "analysis-engine",
		CorrelationID: "corr-1", ConfigVersion: 3, ConfigFingerprint: "abc123",
		Payload: payload,
	}
}

func benchCandidate() opportunity.Candidate {
	return opportunity.Candidate{
		ID: "cand-1", Strategy: "breakoutretest", StrategyVersion: "v1", Symbol: "XAU", Direction: market.Buy,
		Entry:        opportunity.EntryZone{Low: 2000, High: 2002},
		Invalidation: market.PriceLevel{Price: 1990, Label: "structural_low"},
		Targets: []opportunity.Target{
			{Price: market.PriceLevel{Price: 2020, Label: "target_1"}},
			{Price: market.PriceLevel{Price: 2040, Label: "target_2"}},
		},
		Evidence:  []opportunity.Evidence{{Code: "m5_bos_up"}, {Code: "liquidity_high_swept"}},
		Quality:   opportunity.StrategyQuality{Overall: 0.8, Components: map[string]float64{"breakout_quality": 0.9, "retest_quality": 0.7}},
		CreatedAt: 1000, ExpiresAt: 2000,
		Provenance: opportunity.AnalysisProvenance{StructureVersion: "v2", LiquidityVersion: "v1", ZoneVersion: "v1", ConfigVersion: 3, ConfigFingerprint: "bench"},
	}
}

func BenchmarkKafkaEnvelopeEncode(b *testing.B) {
	env := benchEnvelope()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if _, err := kafka.Encode(env); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkKafkaEnvelopeDecode(b *testing.B) {
	encoded, err := kafka.Encode(benchEnvelope())
	if err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		var env kafka.Envelope
		if err := kafka.DecodeStrict(encoded, &env); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkKafkaOpportunityEncode(b *testing.B) {
	candidate := benchCandidate()
	algo := kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		// The exact same adapter+encode path Producer.publish uses —
		// OpportunityPayloadFromCandidate is exported specifically so
		// this benchmark measures the real production code, not a
		// hand-copied near-equivalent.
		payload := kafka.OpportunityPayloadFromCandidate(candidate, algo)
		if _, err := kafka.Encode(payload); err != nil {
			b.Fatal(err)
		}
	}
}

// BenchmarkOpportunityBookDuplicate measures the normal live path: a
// strategy re-observing its still-valid semantic setup must not allocate a
// second lifecycle record or trigger a second publication.
func BenchmarkOpportunityBookDuplicate(b *testing.B) {
	book := opportunity.NewBook()
	candidate := benchCandidate()
	if _, err := book.Observe(candidate, 1001); err != nil {
		b.Fatal(err)
	}
	if _, err := book.Observe(candidate, 1002); err != nil {
		b.Fatal(err)
	}
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := book.Observe(candidate, int64(1003+i)); err != nil {
			b.Fatal(err)
		}
	}
}
