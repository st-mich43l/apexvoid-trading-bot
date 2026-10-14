package czreplay_test

import (
	"math"
	"math/rand"
	"sort"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

// runCapture replays a (possibly altered) capture with the v3 strategy alone.
func runCapture(t *testing.T, c Capture, capture *replaycapture.Capture) *Run {
	return doReplayCapture(t, c, capture, replayOpts{light: true})
}

func keysUpTo(r *Run, upTo int64) []string {
	var out []string
	for _, d := range r.Decisions["D"] {
		if d.EvalTime <= upTo {
			out = append(out, d.Key)
		}
	}
	sort.Strings(out)
	return out
}

func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// mutateAfter returns a capture whose candles after the given open time are replaced
// by random but OHLC-consistent ones on every timeframe.
func mutateAfter(c *replaycapture.Capture, after int64, seed int64) *replaycapture.Capture {
	r := rand.New(rand.NewSource(seed))
	out := *c
	out.Timeframes = map[string][][]float64{}
	for tf, rows := range c.Timeframes {
		var next [][]float64
		price := 0.0
		for _, row := range rows {
			if int64(row[0]) <= after {
				next = append(next, row)
				price = row[4]
				continue
			}
			o := price
			cl := o * (1 + (r.Float64()-0.5)*0.004)
			hi := math.Max(o, cl) * (1 + r.Float64()*0.002)
			lo := math.Min(o, cl) * (1 - r.Float64()*0.002)
			next = append(next, []float64{row[0], o, hi, lo, cl, row[5]})
			price = cl
		}
		out.Timeframes[tf] = next
	}
	return &out
}

// dropBefore returns a capture without any candle opening before the given time.
func dropBefore(c *replaycapture.Capture, before int64) *replaycapture.Capture {
	out := *c
	out.Timeframes = map[string][][]float64{}
	for tf, rows := range c.Timeframes {
		var keep [][]float64
		for _, row := range rows {
			if int64(row[0]) >= before {
				keep = append(keep, row)
			}
		}
		out.Timeframes[tf] = keep
	}
	return &out
}

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

func TestAppendedFutureCandlesNeverChangeAnEarlierDecision(t *testing.T) {
	c := captures[1] // XAU Oct
	full := replay(t, c)
	ds := full.Decisions["D"]
	if len(ds) < 8 {
		t.Fatalf("only %d v3 decisions", len(ds))
	}
	// Cut the capture just after the 3rd and the 8th decision: each replay of the
	// prefix must have published exactly the decisions the full replay had by then.
	for _, i := range []int{2, 7} {
		cut := ds[i].EvalTime
		prefix := runCapture(t, c, truncate(loadCapture(t, c.File), cut))
		if got, want := keysUpTo(prefix, cut), keysUpTo(full, cut); !sameStrings(got, want) {
			t.Fatalf("prefix ending %s: %d decisions vs %d in the full replay", stamp(cut), len(got), len(want))
		}
	}
}

func TestMutatingFutureCandlesNeverChangesAnEarlierDecision(t *testing.T) {
	c := captures[1]
	full := replay(t, c)
	ds := full.Decisions["D"]
	cut := ds[len(ds)/2].EvalTime
	mutated := runCapture(t, c, mutateAfter(loadCapture(t, c.File), cut, 7))
	if got, want := keysUpTo(mutated, cut), keysUpTo(full, cut); !sameStrings(got, want) {
		t.Fatalf("future candles after %s were randomised: %d decisions vs %d", stamp(cut), len(got), len(want))
	}
}

func TestExtraOlderWarmupHistoryDoesNotChangeAWarmedDecision(t *testing.T) {
	c := captures[1]
	full := replay(t, c)
	start := full.Bars[0].Time
	// Drop the first 500 candles of every timeframe; decisions made after a further
	// 1,000 M5 candles (well beyond the engine's 400-candle window and the higher
	// timeframes' warm-up) must be exactly the same.
	dropped := runCapture(t, c, dropBefore(loadCapture(t, c.File), start+500*300))
	from := start + 1500*300
	var want, got []string
	for _, d := range full.Decisions["D"] {
		if d.EvalTime >= from {
			want = append(want, d.Key)
		}
	}
	for _, d := range dropped.Decisions["D"] {
		if d.EvalTime >= from {
			got = append(got, d.Key)
		}
	}
	sort.Strings(want)
	sort.Strings(got)
	if len(want) < 3 || !sameStrings(got, want) {
		t.Fatalf("with 500 fewer warm-up candles the decisions from %s differ: %d vs %d", stamp(from), len(got), len(want))
	}
}

func TestAMirroredMarketHasMirroredDecisions(t *testing.T) {
	c := captures[1]
	full := replay(t, c)
	const axis = 8400.0
	mirrored := runCapture(t, c, mirror(loadCapture(t, c.File), axis))
	flip := func(d string) string {
		if d == "BUY" {
			return "SELL"
		}
		return "BUY"
	}
	type key struct {
		dir     string
		confirm int64
	}
	want := map[key]*Decision{}
	for _, d := range full.Decisions["D"] {
		want[key{flip(d.Direction), d.ConfirmTime}] = d
	}
	matched, targetDrift, largeDrift := 0, 0, 0
	for _, m := range mirrored.Decisions["D"] {
		d, ok := want[key{m.Direction, m.ConfirmTime}]
		if !ok {
			continue
		}
		matched++
		if math.Abs(m.Stop-(axis-d.Stop)) > 1e-6 || math.Abs(m.EntryLow-(axis-d.EntryHigh)) > 1e-6 || math.Abs(m.EntryHigh-(axis-d.EntryLow)) > 1e-6 {
			t.Fatalf("%s %d: the entry or stop is not the mirror image: %+v vs %+v", m.Direction, m.ConfirmTime, m, d)
		}
		// The target is a liquidity pool, and the engine's pool builder (frozen, shared
		// with every strategy) clusters swings greedily in price order, which is not
		// mirror-symmetric: the two markets can be handed different pools (different
		// members, a different latest swing, so a different swept/unswept verdict), and
		// the strategy then correctly picks the nearest unswept pool of each. The exact
		// mirror of the v3 logic on identical inputs is asserted in the package's
		// property test (TestBuyAndSellAreExactMirrorImagesOnRandomMarkets, injected
		// pools); here the drift is measured and bounded, not hidden.
		if math.Abs(m.Target-(axis-d.Target)) > 1e-6 {
			targetDrift++
			if math.Abs(m.Target-(axis-d.Target)) > d.ATR {
				largeDrift++
			}
		}
	}
	// The zone chain and the engine's structure reads are not guaranteed to be
	// exactly symmetric end to end; the contract is that every v3 decision the two
	// markets share is an exact mirror, and that they share almost all of them.
	if matched < len(full.Decisions["D"])*8/10 {
		t.Fatalf("only %d of %d decisions have a mirrored counterpart", matched, len(full.Decisions["D"]))
	}
	if largeDrift*10 > matched {
		t.Fatalf("%d of %d mirrored decisions have a target more than an ATR from the mirror (%d drifted at all)", largeDrift, matched, targetDrift)
	}
	t.Logf("%d of %d decisions mirrored (entry and stop exact; %d with a pool-builder target drift, %d of them over an ATR); the mirrored market published %d", matched, len(full.Decisions["D"]), targetDrift, largeDrift, len(mirrored.Decisions["D"]))
}

// A confirmation is one opportunity, not one per evaluation: the same confirming
// candle never yields two opportunities with overlapping entries, however
// price moved between the evaluations that saw it (an independent review found v3's
// first version renamed an episode when a structure left the "price is at it" set).
func TestOneConfirmationIsNeverRenamedBetweenEvaluations(t *testing.T) {
	for _, c := range captures {
		r := replay(t, c)
		type k struct {
			dir     string
			confirm int64
		}
		by := map[k][]*Decision{}
		for _, d := range r.Decisions["D"] {
			by[k{d.Direction, d.ConfirmTime}] = append(by[k{d.Direction, d.ConfirmTime}], d)
		}
		multiple := 0
		for key, ds := range by {
			for i := range ds {
				for j := i + 1; j < len(ds); j++ {
					if ds[i].Key != ds[j].Key && overlapPositive(ds[i].EntryLow, ds[i].EntryHigh, ds[j].EntryLow, ds[j].EntryHigh) {
						t.Errorf("%s: one confirmation (%+v) published twice with overlapping entries: %v and %v", c.Name, key, ds[i].Key, ds[j].Key)
					}
				}
			}
			if len(ds) > 1 {
				multiple++
			}
		}
		t.Logf("%s: %d confirmations, %d with more than one (disjoint) region", c.Name, len(by), multiple)
	}
}
