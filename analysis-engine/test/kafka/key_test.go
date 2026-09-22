package kafka_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// TestRecordKey_SameSymbolDifferentTimeframesShareOneKey proves source
// task §14/§15: XAU M1, XAU M5, XAU M15, XAU H1 must all use the
// identical record key — never timeframe-qualified — so they land in
// the same partition and preserve total per-symbol ordering.
func TestRecordKey_SameSymbolDifferentTimeframesShareOneKey(t *testing.T) {
	timeframes := []market.Timeframe{market.M1, market.M5, market.M15, market.H1}
	var keys [][]byte
	for _, tf := range timeframes {
		_ = tf // timeframe is deliberately NOT an input to RecordKey at all
		keys = append(keys, kafka.RecordKey("XAU"))
	}
	for i := 1; i < len(keys); i++ {
		if string(keys[i]) != string(keys[0]) {
			t.Errorf("timeframe %v produced a different key (%q) than timeframe %v (%q) — record key must be symbol-only",
				timeframes[i], keys[i], timeframes[0], keys[0])
		}
	}
	if string(keys[0]) != "XAU" {
		t.Errorf("expected the record key to be exactly the canonical symbol, got %q", keys[0])
	}
}

func TestRecordKey_DifferentSymbolsProduceDifferentKeys(t *testing.T) {
	xau := kafka.RecordKey("XAU")
	eurusd := kafka.RecordKey("EURUSD")
	if string(xau) == string(eurusd) {
		t.Error("expected different symbols to produce different record keys")
	}
}

func TestBarIdentity_StringIsDeterministicAndSymbolTimeframeCloseTimeScoped(t *testing.T) {
	a := kafka.BarIdentity{Symbol: "XAU", Timeframe: market.M5, CloseTime: 1_700_000_300}
	b := kafka.BarIdentity{Symbol: "XAU", Timeframe: market.M5, CloseTime: 1_700_000_300}
	if a.String() != b.String() {
		t.Errorf("expected identical BarIdentity values to produce identical strings: %q vs %q", a.String(), b.String())
	}
	c := kafka.BarIdentity{Symbol: "XAU", Timeframe: market.M5, CloseTime: 1_700_000_600} // different close time -> different bar
	if a.String() == c.String() {
		t.Error("expected a different close_time to produce a different bar identity")
	}
}
