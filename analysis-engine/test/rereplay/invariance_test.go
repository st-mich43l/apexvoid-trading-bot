package rereplay_test

import (
	"fmt"
	"math"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// mirror reflects every price about axis (p -> axis - p) on every timeframe.
func mirror(c *replaycapture.Capture, axis float64) *replaycapture.Capture {
	out := *c
	out.Timeframes = map[string][][]float64{}
	for tf, rows := range c.Timeframes {
		next := make([][]float64, len(rows))
		for i, row := range rows {
			next[i] = []float64{row[0], axis - row[1], axis - row[3], axis - row[2], axis - row[4], row[5]}
		}
		out.Timeframes[tf] = next
	}
	return &out
}

func decisionKeys(ds []*Decision, upTo int64) []string {
	var out []string
	for _, d := range ds {
		if d.EvalTime <= upTo {
			out = append(out, fmt.Sprintf("%s|%s|%d|%.5f|%.5f", d.ID, d.Direction, d.EvalTime, d.EntryLow, d.EntryHigh))
		}
	}
	sort.Strings(out)
	return out
}

// A decision at a candle never depends on a later one: the replay of a prefix has
// published, by then, exactly what the full replay had.
func TestAppendedFutureCandlesNeverChangeAnEarlierDecision(t *testing.T) {
	c := captures[0] // USDJPY
	full := replay(t, c)
	if len(full.Decisions) < 20 {
		t.Fatalf("only %d decisions", len(full.Decisions))
	}
	for _, i := range []int{10, 25, 40} {
		cut := full.Decisions[i].EvalTime
		prefix := doReplay(t, c, truncate(loadCapture(t, c.File), cut), nil)
		got, want := decisionKeys(prefix.Decisions, cut), decisionKeys(full.Decisions, cut)
		if fmt.Sprint(got) != fmt.Sprint(want) {
			t.Fatalf("prefix ending %s: %d decisions against %d in the full replay", utc(cut), len(got), len(want))
		}
	}
}

// The reflected market publishes the mirrored decisions. The v3 integrity logic is an
// exact mirror on identical inputs (the unit tests assert it); end to end the frozen
// scalp structure builder is not exactly symmetric (it clusters contacts greedily in
// ascending price order, so a reflected market can cluster a contact differently and
// move a barrier by a fraction of an ATR, and the swings it reads differ). The contract
// here is that every decision the two markets share is within an ATR of the mirror.
func TestAMirroredMarketHasMirroredDecisions(t *testing.T) {
	c := captures[1] // EURUSD
	full := replay(t, c)
	const axis = 2.5
	mirrored := doReplay(t, c, mirror(loadCapture(t, c.File), axis), nil)
	flip := func(d string) string {
		if d == "BUY" {
			return "SELL"
		}
		return "BUY"
	}
	want := map[string]*Decision{}
	for _, d := range full.Decisions {
		want[fmt.Sprintf("%s|%d", flip(d.Direction), d.ConfirmTime)] = d
	}
	matched, exact := 0, 0
	for _, m := range mirrored.Decisions {
		d, ok := want[fmt.Sprintf("%s|%d", m.Direction, m.ConfirmTime)]
		if !ok {
			continue
		}
		matched++
		off := math.Max(math.Abs(m.Invalidate-(axis-d.Invalidate)), math.Max(math.Abs(m.EntryLow-(axis-d.EntryHigh)), math.Abs(m.EntryHigh-(axis-d.EntryLow))))
		if off > d.ATR {
			t.Fatalf("%s %d: %.5f from the mirror (> one ATR): %+v vs %+v", m.Direction, m.ConfirmTime, off, m, d)
		}
		if off < 1e-6 {
			exact++
		}
	}
	// How many decisions the two markets share is a property of the frozen structure
	// builder, not of v3: measured and logged, not asserted (about a third on EURUSD).
	if matched == 0 {
		t.Fatal("the reflected market shares no decision with the original")
	}
	t.Logf("%d of %d decisions mirrored (%d exactly); the mirrored market published %d", matched, len(full.Decisions), exact, len(mirrored.Decisions))
}

// Replaying the same capture twice gives the same decisions: nothing depends on map
// iteration order or the wall clock.
func TestTheReplayIsDeterministic(t *testing.T) {
	c := captures[2] // GBPUSD
	a, b := replay(t, c), doReplay(t, c, loadCapture(t, c.File), nil)
	if fmt.Sprint(decisionKeys(a.Decisions, math.MaxInt64)) != fmt.Sprint(decisionKeys(b.Decisions, math.MaxInt64)) {
		t.Fatal("two replays of one capture disagree")
	}
}
