package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
)

type levelArgs struct {
	sessions         []string
	round, reactions int
	span, departure  float64
}

func (a levelArgs) book() keylevel.BookLevel {
	return keylevel.BookLevel{Sessions: a.sessions, RoundClass: a.round, Reactions: a.reactions, SpanHours: a.span, MaxDepartureRefATR: a.departure}
}

// envelope is a minimal analysis.opportunity.v1 line, enough for the attribution keys.
func envelope(strategy, direction string, createdAt int64, low float64) string {
	return fmt.Sprintf(`{"event_type":"analysis.opportunity.v1","payload":{"id":"opp_%s_%d_%.2f","strategy":%q,"direction":%q,"created_at":%d,"entry":{"low":%.2f,"high":%.2f}}}`,
		direction, createdAt, low, strategy, direction, createdAt, low, low+3)
}

// fakeBuild writes a shell script that behaves like cmd/replay -envelopes-out: it writes the given lines to the path after -envelopes-out.
func fakeBuild(t *testing.T, dir, name string, lines []string) string {
	t.Helper()
	body := "#!/bin/sh\nout=\"\"\nwhile [ $# -gt 0 ]; do\n  if [ \"$1\" = \"-envelopes-out\" ]; then out=\"$2\"; fi\n  shift\ndone\ncat > \"$out\" <<'EOF'\n" + strings.Join(lines, "\n") + "\nEOF\n"
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// Two builds on one tiny capture: build b drops the 300 SELL and adds a BUY at 600; everything else is the same setup under a different id.
func TestAttributionListsTheKnownSetupDifferenceDeterministically(t *testing.T) {
	dir := t.TempDir()
	capture := filepath.Join(dir, "tiny.json")
	if err := os.WriteFile(capture, []byte(`{"version":1,"symbol":"XAU","columns":["t","open","high","low","close","volume"],"timeframes":{"M5":[[0,1,2,0.5,1.5,1],[300,1,2,0.5,1.5,1]]}}`), 0o644); err != nil {
		t.Fatal(err)
	}
	a := fakeBuild(t, dir, "replay-a", []string{
		envelope("key_level", "BUY", 0, 100), envelope("key_level", "SELL", 300, 110), envelope("fvg", "BUY", 300, 90), envelope("key_level", "BUY", 0, 104),
	})
	b := fakeBuild(t, dir, "replay-b", []string{
		envelope("key_level", "BUY", 0, 100.5), envelope("key_level", "BUY", 600, 120), envelope("key_level", "BUY", 0, 104),
	})
	run := func(out string) []byte {
		if err := runAttribution([]string{"-out", out, "-build", "a=" + a + ",cfg-a", "-build", "b=" + b + ",cfg-b", "-capture", "tiny=" + capture}); err != nil {
			t.Fatal(err)
		}
		raw, err := os.ReadFile(filepath.Join(out, "attribution.json"))
		if err != nil {
			t.Fatal(err)
		}
		return raw
	}
	first := run(filepath.Join(dir, "out1"))
	var report attrReport
	if err := json.Unmarshal(first, &report); err != nil {
		t.Fatal(err)
	}
	c := report.Captures[0]
	if c.Setups["a"] != 3 || c.Setups["b"] != 3 {
		t.Fatalf("key_level setups per build: %v (the fvg setup is not counted)", c.Setups)
	}
	want := attrStep{From: "a", To: "b", Removed: []string{"SELL|300|0"}, Added: []string{"BUY|600|0"}}
	if !reflect.DeepEqual(c.Steps, []attrStep{want}) {
		t.Fatalf("steps %+v, want %+v", c.Steps, want)
	}
	if c.Bars["M5"] != 2 || c.Last["M5"] != 300 {
		t.Fatalf("coverage %+v", c)
	}
	// the labeller input keeps every setup with its key, in key order
	raw, err := os.ReadFile(filepath.Join(dir, "out1", "tiny", "a.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var keys []string
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var s struct {
			Key      string          `json:"key"`
			Creation json.RawMessage `json:"creation"`
			Terminal json.RawMessage `json:"terminal"`
		}
		if err := json.Unmarshal([]byte(line), &s); err != nil {
			t.Fatal(err)
		}
		if string(s.Terminal) != "null" || len(s.Creation) == 0 {
			t.Fatalf("labeller shape: %s", line)
		}
		keys = append(keys, s.Key)
	}
	if !reflect.DeepEqual(keys, []string{"BUY|0|0", "BUY|0|1", "SELL|300|0"}) {
		t.Fatalf("keys %v", keys)
	}
	// same inputs, same bytes
	second := run(filepath.Join(dir, "out2"))
	if string(first) != string(second) {
		t.Fatalf("attribution is not deterministic:\n%s\n%s", first, second)
	}
}

func TestMinorReasonNamesTheFirstRuleTheLevelFails(t *testing.T) {
	th := majorThresholds{swingMinReactions: 2, roundMinReactions: 1, swingMinSpanHours: 8, swingMinDeparture: 1.5}
	cases := []struct {
		level levelArgs
		want  string
	}{
		{levelArgs{sessions: []string{"PDL"}}, "session_level_broken"},
		{levelArgs{round: 50, reactions: 1}, "round_level_broken"},
		{levelArgs{round: 100}, "round_without_reaction"},
		{levelArgs{round: 10, reactions: 1}, "reactions"},
		{levelArgs{reactions: 3, span: 4}, "span"},
		{levelArgs{reactions: 3, span: 9, departure: 1.2}, "departure"},
		{levelArgs{reactions: 3, span: 9, departure: 2}, "broken"},
	}
	for _, c := range cases {
		if got := minorReason(c.level.book(), th); got != c.want {
			t.Errorf("%+v: %s, want %s", c.level, got, c.want)
		}
	}
}
