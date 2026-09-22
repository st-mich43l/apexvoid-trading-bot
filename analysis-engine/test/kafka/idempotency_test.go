// Duplicate delivery handling (source task §17/§54): the same closed-bar
// identity may legitimately arrive twice under at-least-once delivery
// (ADR-009). marketdata.TimeframeHistory.Append (not a Kafka-specific
// dedup table — see docs/transport/kafka.md's "Duplicate delivery
// handling" section for why) is what actually absorbs this; these tests
// prove it does so correctly for both the safe case (identical repeat)
// and the unsafe case (same identity, different payload).
package kafka_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func TestTimeframeHistory_IdenticalRedeliveryIsAnOrdinaryDuplicate(t *testing.T) {
	h := marketdata.NewTimeframeHistory("XAU", market.M5, 10, false)
	c := market.Candle{Time: 100, Open: 2000, High: 2005, Low: 1998, Close: 2003, Volume: 10}

	first, err := h.Append(c)
	if err != nil || first != marketdata.AppendAccepted {
		t.Fatalf("first append: result=%v err=%v, want Accepted/nil", first, err)
	}
	second, err := h.Append(c) // exact redelivery — at-least-once, same payload
	if err != nil {
		t.Fatalf("second append: unexpected error %v", err)
	}
	if second != marketdata.AppendDuplicate {
		t.Errorf("expected an identical redelivery to report AppendDuplicate, got %v", second)
	}
	if h.Len() != 1 {
		t.Errorf("expected the duplicate to be ignored (Len still 1), got Len=%d", h.Len())
	}
}

func TestTimeframeHistory_SameIdentityDifferentOHLCIsAConflictNotADuplicate(t *testing.T) {
	h := marketdata.NewTimeframeHistory("XAU", market.M5, 10, false)
	original := market.Candle{Time: 100, Open: 2000, High: 2005, Low: 1998, Close: 2003, Volume: 10}
	corrected := market.Candle{Time: 100, Open: 2000, High: 2010, Low: 1998, Close: 2008, Volume: 15} // same identity, different OHLC — a correction

	if _, err := h.Append(original); err != nil {
		t.Fatalf("first append failed: %v", err)
	}
	result, err := h.Append(corrected)
	if err != nil {
		t.Fatalf("second append: unexpected error %v", err)
	}
	if result != marketdata.AppendConflict {
		t.Errorf("expected a same-identity-different-payload append to report AppendConflict (source task §17), got %v", result)
	}
	// Correction policy (docs/transport/kafka.md): the ORIGINAL candle
	// is retained — a conflicting payload never silently rewrites
	// already-analyzed history.
	if got := h.Newest(); got != original {
		t.Errorf("expected the original candle to be retained on conflict, got %+v (original was %+v)", got, original)
	}
	if h.Len() != 1 {
		t.Errorf("expected a conflict to neither be appended nor replace anything (Len still 1), got Len=%d", h.Len())
	}
}

func TestTimeframeHistory_AllowReplaceTrueStillReplacesOnAnyPayloadDifference(t *testing.T) {
	// The forming-bar (allowReplace=true) mode is a DIFFERENT policy
	// from the Kafka closed-bar consumer's own (allowReplace=false) —
	// this test exists so the two are never confused: a live-updating
	// forming bar legitimately changes OHLC at the same timestamp, and
	// that must keep working exactly as before this task's own
	// AppendConflict addition.
	h := marketdata.NewTimeframeHistory("XAU", market.M1, 10, true)
	first := market.Candle{Time: 100, Open: 2000, High: 2001, Low: 1999, Close: 2000, Volume: 1}
	updated := market.Candle{Time: 100, Open: 2000, High: 2003, Low: 1999, Close: 2002, Volume: 4}

	if _, err := h.Append(first); err != nil {
		t.Fatalf("first append failed: %v", err)
	}
	result, err := h.Append(updated)
	if err != nil {
		t.Fatalf("second append: unexpected error %v", err)
	}
	if result != marketdata.AppendReplaced {
		t.Errorf("expected AppendReplaced when allowReplace=true, got %v", result)
	}
	if got := h.Newest(); got != updated {
		t.Errorf("expected the forming bar to be replaced with the updated values, got %+v", got)
	}
}

// TestBarEventFromPayload_SameIdentityDifferentPayloadStillFlowsThroughAsAConflict
// proves the Kafka adapter path (payload -> BarEventFromPayload ->
// history.Append) produces the same AppendConflict outcome as feeding
// market.Candle directly — the adapter does not accidentally mask a
// conflict.
func TestBarEventFromPayload_SameIdentityDifferentPayloadStillFlowsThroughAsAConflict(t *testing.T) {
	h := marketdata.NewTimeframeHistory("XAU", market.M5, 10, false)

	first := kafka.BarClosedPayload{
		CanonicalSymbol: "XAU", Timeframe: "M5", OpenTime: 100, CloseTime: 400,
		Open: 2000, High: 2005, Low: 1998, Close: 2003, Volume: 10,
	}
	conflicting := first
	conflicting.High = 2020 // a correction to the same bar identity

	firstEvent, err := kafka.BarEventFromPayload(first)
	if err != nil {
		t.Fatalf("BarEventFromPayload(first) failed: %v", err)
	}
	if _, err := h.Append(firstEvent.Candle); err != nil {
		t.Fatalf("append of first event failed: %v", err)
	}

	secondEvent, err := kafka.BarEventFromPayload(conflicting)
	if err != nil {
		t.Fatalf("BarEventFromPayload(conflicting) failed: %v", err)
	}
	result, err := h.Append(secondEvent.Candle)
	if err != nil {
		t.Fatalf("append of conflicting event failed: %v", err)
	}
	if result != marketdata.AppendConflict {
		t.Errorf("expected AppendConflict through the Kafka adapter path, got %v", result)
	}
}

func TestBarIdentityFromPayload_UsesCloseTimeNotOpenTime(t *testing.T) {
	p := kafka.BarClosedPayload{CanonicalSymbol: "XAU", Timeframe: "M5", OpenTime: 100, CloseTime: 400}
	id := kafka.BarIdentityFromPayload(p)
	if id.CloseTime != 400 {
		t.Errorf("expected BarIdentity.CloseTime to be the payload's close_time (400), got %d", id.CloseTime)
	}
	if id.Symbol != "XAU" || id.Timeframe != "M5" {
		t.Errorf("expected identity symbol/timeframe to match the payload, got %+v", id)
	}
}
