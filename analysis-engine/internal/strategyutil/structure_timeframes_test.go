package strategyutil

import (
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

var allMultiFrame = []market.Timeframe{market.M5, market.M15, market.H1}

func TestParseStructureTimeframesReturnsAnOrderedDistinctList(t *testing.T) {
	for name, raw := range map[string]any{
		"any list":    []any{"M5", "M15", "H1"},
		"string list": []string{"M5", "M15", "H1"},
		"typed list":  []market.Timeframe{market.M5, market.M15, market.H1},
	} {
		got, err := ParseStructureTimeframes(map[string]any{"structure_timeframes": raw}, allMultiFrame)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if len(got) != 3 || got[0] != market.M5 || got[1] != market.M15 || got[2] != market.H1 {
			t.Fatalf("%s: got %v", name, got)
		}
	}
	got, err := ParseStructureTimeframes(map[string]any{"structure_timeframes": []any{"H1", "M5"}}, allMultiFrame)
	if err != nil || len(got) != 2 || got[0] != market.H1 || got[1] != market.M5 {
		t.Fatalf("the written order must be kept: %v %v", got, err)
	}
	if got, err := ParseStructureTimeframes(map[string]any{"structure_timeframes": []any{"m15"}}, allMultiFrame); err != nil || got[0] != market.M15 {
		t.Fatalf("a lower-case name is the same frame: %v %v", got, err)
	}
}

func TestParseStructureTimeframesRefusesM1AlwaysAndAnythingOutsideAllowed(t *testing.T) {
	cases := []struct {
		name    string
		params  map[string]any
		allowed []market.Timeframe
		want    string
	}{
		{"M1", map[string]any{"structure_timeframes": []any{"M1", "M5"}}, allMultiFrame, "M1 is execution only"},
		{"M1 even when the strategy allows it", map[string]any{"structure_timeframes": []any{"M1"}}, []market.Timeframe{market.M1, market.M5}, "M1 is execution only"},
		{"not allowed", map[string]any{"structure_timeframes": []any{"M5", "H4"}}, allMultiFrame, "H4 is not allowed"},
		{"M5 where the strategy has no M5", map[string]any{"structure_timeframes": []any{"M5"}}, []market.Timeframe{market.M15, market.H1}, "M5 is not allowed"},
		{"unknown name", map[string]any{"structure_timeframes": []any{"M7"}}, allMultiFrame, "not a timeframe"},
		{"duplicate", map[string]any{"structure_timeframes": []any{"M5", "M5"}}, allMultiFrame, "twice"},
		{"empty", map[string]any{"structure_timeframes": []any{}}, allMultiFrame, "at least one"},
		{"missing", map[string]any{}, allMultiFrame, "is required"},
		{"not a list", map[string]any{"structure_timeframes": "M5"}, allMultiFrame, "must be a list"},
		{"not a string", map[string]any{"structure_timeframes": []any{5}}, allMultiFrame, "timeframe names"},
	}
	for _, c := range cases {
		_, err := ParseStructureTimeframes(c.params, c.allowed)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: want an error containing %q, got %v", c.name, c.want, err)
		}
	}
}

func TestParseRequireConfirmationOverridesTheGlobalOnlyWhenPresent(t *testing.T) {
	for _, global := range []bool{false, true} {
		if got, err := ParseRequireConfirmation(map[string]any{}, global); err != nil || got != global {
			t.Fatalf("absent must return the global %v, got %v %v", global, got, err)
		}
		if got, err := ParseRequireConfirmation(map[string]any{"require_confirmation": true}, global); err != nil || !got {
			t.Fatalf("a strategy's own true overrides global %v: %v %v", global, got, err)
		}
		if got, err := ParseRequireConfirmation(map[string]any{"require_confirmation": false}, global); err != nil || got {
			t.Fatalf("a strategy's own false overrides global %v: %v %v", global, got, err)
		}
	}
	if _, err := ParseRequireConfirmation(map[string]any{"require_confirmation": "yes"}, false); err == nil {
		t.Fatal("a non-boolean value must be refused")
	}
	if !GlobalConfirmation(map[string]any{"require_candle_confirmation": true}) || GlobalConfirmation(map[string]any{}) {
		t.Fatal("the injected global must be read, and absent means false")
	}
}

func TestPreferHigherTimeframeOrdersByFrameThenStarsThenDistance(t *testing.T) {
	h1 := ThesisRank{market.H1, 2, 9}
	m15 := ThesisRank{market.M15, 3, 1}
	m5 := ThesisRank{market.M5, 3, 0.5}
	if !PreferHigherTimeframe(h1, m15) || PreferHigherTimeframe(m15, h1) {
		t.Fatal("the higher timeframe comes first, whatever the stars and the distance")
	}
	if !PreferHigherTimeframe(m15, m5) {
		t.Fatal("M15 before M5")
	}
	if !PreferHigherTimeframe(ThesisRank{market.M15, 3, 5}, ThesisRank{market.M15, 2, 1}) {
		t.Fatal("on one frame, more stars first")
	}
	if !PreferHigherTimeframe(ThesisRank{market.M15, 2, 1}, ThesisRank{market.M15, 2, 5}) {
		t.Fatal("on one frame and equal stars, the nearer entry first")
	}
	same := ThesisRank{market.M5, 2, 1}
	if PreferHigherTimeframe(same, same) {
		t.Fatal("a full tie prefers neither, so a stable sort keeps the earlier one")
	}
}
