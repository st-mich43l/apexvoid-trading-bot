package kafka

import (
	"fmt"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
)

// BarEventFromPayload adapts a decoded, envelope-unwrapped
// BarClosedPayload into marketdata.BarEvent — the one explicit place a
// Kafka wire shape becomes the internal domain model (source task §40:
// "do not let Kafka schema types become the internal domain model").
// Nothing in internal/marketdata knows this package exists — rank 1
// cannot import rank 7, so the conversion can only ever happen here,
// going this direction.
//
// This is also where contracts/market/bar-closed-v1.schema.json's
// semantic rules that JSON Schema's structural checks alone cannot
// express get applied (source task §27): OHLC ordering, finiteness, and
// negative volume are all checked by reusing marketdata.ValidateCandle
// — the SAME function every other candle ingestion path in this engine
// already uses, rather than a second, independently-drifting copy of
// those rules living in the transport layer.
func BarEventFromPayload(p BarClosedPayload) (marketdata.BarEvent, error) {
	if p.CanonicalSymbol == "" {
		return marketdata.BarEvent{}, fmt.Errorf("kafka: bar-closed payload missing canonical_symbol")
	}
	tf, err := market.ParseTimeframe(p.Timeframe)
	if err != nil {
		return marketdata.BarEvent{}, fmt.Errorf("kafka: bar-closed payload: %w", err)
	}
	minutes, _ := tf.Minutes() // ParseTimeframe already proved tf is recognized, so this always succeeds
	expectedClose := p.OpenTime + int64(minutes)*60
	if p.CloseTime != expectedClose {
		return marketdata.BarEvent{}, fmt.Errorf(
			"kafka: bar-closed payload: close_time %d does not equal open_time %d + %s duration (%ds) = %d",
			p.CloseTime, p.OpenTime, tf, int64(minutes)*60, expectedClose,
		)
	}
	candle := market.Candle{
		Time: p.OpenTime, Open: p.Open, High: p.High, Low: p.Low, Close: p.Close, Volume: p.Volume,
	}
	if err := marketdata.ValidateCandle(candle); err != nil {
		return marketdata.BarEvent{}, fmt.Errorf("kafka: bar-closed payload: %w", err)
	}
	return marketdata.BarEvent{
		Symbol: market.Symbol(p.CanonicalSymbol), Timeframe: tf, Candle: candle,
	}, nil
}

// BarIdentityFromPayload returns the payload's deterministic dedup
// identity (source task §16) directly from the wire shape, without
// requiring a full BarEventFromPayload conversion to succeed first —
// used by the consumer to log/trace a rejected payload's identity even
// when it fails validation.
func BarIdentityFromPayload(p BarClosedPayload) BarIdentity {
	return BarIdentity{
		Symbol:    market.Symbol(p.CanonicalSymbol),
		Timeframe: market.Timeframe(p.Timeframe),
		CloseTime: p.CloseTime,
	}
}

// OpportunityPayloadFromCandidate adapts the internal domain type to the
// wire shape — the producer-side direction of the same adapter
// responsibility (source task §40 applies symmetrically). Never the
// reverse: internal/opportunity does not know this package exists —
// rank 5 cannot import rank 7. Exported so both Producer.publish and
// test/benchmark's BenchmarkKafkaOpportunityEncode exercise the exact
// same conversion — one canonical implementation, not a benchmark-only
// near-copy.
func OpportunityPayloadFromCandidate(c opportunity.Candidate, algo AlgorithmVersion) OpportunityPayload {
	targets := make([]TargetPayload, len(c.Targets))
	for i, t := range c.Targets {
		targets[i] = TargetPayload{Price: PriceLevelPayload{Price: float64(t.Price.Price), Label: t.Price.Label}}
	}
	evidence := make([]EvidencePayload, len(c.Evidence))
	for i, e := range c.Evidence {
		evidence[i] = EvidencePayload{Code: e.Code}
	}
	return OpportunityPayload{
		ID: c.ID, Strategy: string(c.Strategy), Symbol: string(c.Symbol), Direction: string(c.Direction),
		Entry:            EntryZonePayload{Low: c.Entry.Low, High: c.Entry.High},
		Invalidation:     PriceLevelPayload{Price: float64(c.Invalidation.Price), Label: c.Invalidation.Label},
		Targets:          targets,
		Evidence:         evidence,
		Quality:          QualityPayload{Overall: c.Quality.Overall, Components: c.Quality.Components},
		AlgorithmVersion: AlgorithmVersionPayload{Structure: algo.Structure, Liquidity: algo.Liquidity},
		CreatedAt:        c.CreatedAt, ExpiresAt: c.ExpiresAt,
	}
}
