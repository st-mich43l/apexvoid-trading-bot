package rereplay_test

import (
	"encoding/json"
	"os"
	"testing"
)

// TestDumpBaseline writes the decisions of the code under test, for every capture.
// It was run on master (Range Edge v2) to produce testdata/range-edge-v2-baseline.json:
//
//	RE_DUMP_BASELINE=$PWD/testdata/range-edge-v2-baseline.json go test ./test/rereplay -run TestDumpBaseline
func TestDumpBaseline(t *testing.T) {
	out := os.Getenv("RE_DUMP_BASELINE")
	if out == "" {
		t.Skip("set RE_DUMP_BASELINE to regenerate")
	}
	all := map[string][]*Decision{}
	for _, c := range captures {
		all[c.Name] = replay(t, c).Decisions
	}
	raw, err := json.MarshalIndent(all, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(out, append(raw, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
}
