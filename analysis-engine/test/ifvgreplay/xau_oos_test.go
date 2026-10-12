package ifvgreplay_test

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// outOfSampleFrom is 6 Oct 2026 06:00 UTC, after the last of the five losing XAU
// Session Level trades and after the iFVG v3 design was fixed. The capture
// replay-xau-production-capture-20261012.json (read-only from production on 12 Oct)
// holds M5 from 1 Oct 01:55: its first part repeats the incident window, its second is
// the out-of-sample XAU window the enablement review uses.
const outOfSampleFrom = int64(1791266400)

// TestIFVGReplayOnTheOutOfSampleXAUWindow reports what iFVG v2 (frozen) and v3 do on
// the post-incident XAU candles. Reproduce with
// IFVG_REPORT_DIR=/tmp/ifvg go test ./test/ifvgreplay -run OutOfSample -v.
func TestIFVGReplayOnTheOutOfSampleXAUWindow(t *testing.T) {
	dir := os.Getenv("IFVG_REPORT_DIR")
	if dir == "" {
		t.Skip("IFVG_REPORT_DIR not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc := resolveDoc(t)
	res, err := runCapture(doc, "replay-xau-production-capture-20261012.json", "XAU 1-12 Oct", "extended")
	if err != nil {
		t.Fatal(err)
	}
	after := func(r row) bool { return r.ObservedAt >= outOfSampleFrom }
	before := func(r row) bool { return r.ObservedAt < outOfSampleFrom }
	var b strings.Builder
	fmt.Fprintf(&b, "# iFVG on the 1-12 Oct XAU capture (hypothetical, causal fills)\n\n- evaluations %d, engine vs offline mismatches %d\n", res.Evaluations, res.EngineOfflineMismatches)
	for _, w := range []struct {
		name string
		keep func(row) bool
	}{{"1-6 Oct (incident window, repeated)", before}, {"6 Oct 06:00 - 12 Oct (out of sample)", after}} {
		fmt.Fprintf(&b, "\n## %s\n\n- v2: %s\n- v3: %s\n", w.name, fmtStats(summarise(res.V2, w.keep)), fmtStats(summarise(res.V3, w.keep)))
		var v2, v3 []float64
		for _, r := range res.V2 {
			if w.keep(r) {
				v2 = append(v2, rValues([]row{r}, nil)...)
			}
		}
		for _, r := range res.V3 {
			if w.keep(r) {
				v3 = append(v3, rValues([]row{r}, nil)...)
			}
		}
		fmt.Fprintf(&b, "- v2 R: %s\n- v3 R: %s\n", fmtR(v2), fmtR(v3))
	}
	out := b.String()
	if err := os.WriteFile(filepath.Join(dir, "oos-summary.md"), []byte(out), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Log("\n" + out)
	if res.EngineOfflineMismatches != 0 {
		t.Errorf("the engine and the offline detector disagreed on %d evaluations", res.EngineOfflineMismatches)
	}
	for i, f := range res.V3Facts {
		if f.AnyV3Defect {
			t.Errorf("v3 published a setup that misses its own contract: %d %+v", i, f)
		}
	}
}
