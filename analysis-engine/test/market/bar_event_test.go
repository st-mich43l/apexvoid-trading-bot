package market_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
)

func TestBarEventPublicationDependsOnOrigin(t *testing.T) {
	for _, tc := range []struct {
		name   string
		origin marketdata.EventOrigin
		want   bool
	}{
		{name: "zero value remains live for direct callers", want: true},
		{name: "live", origin: marketdata.EventOriginLive, want: true},
		{name: "bootstrap", origin: marketdata.EventOriginBootstrap, want: false},
		{name: "replay", origin: marketdata.EventOriginReplay, want: false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := (marketdata.BarEvent{Origin: tc.origin}).PublishesOpportunity(); got != tc.want {
				t.Fatalf("PublishesOpportunity() = %v, want %v", got, tc.want)
			}
		})
	}
}
