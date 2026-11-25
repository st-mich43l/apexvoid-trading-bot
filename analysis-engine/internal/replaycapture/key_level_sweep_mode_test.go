package replaycapture_test

// Key Level v14 (PR-KL15): on XAU (level_model major) major_sweep_mode optional publishes a confirmed M5 reaction at a MAJOR level whether or not it swept;
// required is v13 exactly. Fixtures are the real XAU captures of KL13.

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
)

const xauPip = 0.1

func sweptOf(t *testing.T, c opportunity.Candidate) bool {
	t.Helper()
	swept, unswept := evidenceHas(c, keylevel.ReactionSweptEvidence), evidenceHas(c, keylevel.ReactionUnsweptEvidence)
	if swept == unswept {
		t.Fatalf("a v14 candidate carries exactly one of swept / unswept: %s %v", c.ID, c.Evidence)
	}
	want := 0.0
	if swept {
		want = 1
	}
	if v, ok := c.Quality.Components["kl_reaction_swept"]; !ok || v != want {
		t.Fatalf("kl_reaction_swept %v (present %v), want %v", v, ok, want)
	}
	return swept
}

func stopPips(c opportunity.Candidate) float64 {
	if c.Direction == market.Buy {
		return (c.Entry.High - float64(c.Invalidation.Price)) / xauPip
	}
	return (float64(c.Invalidation.Price) - c.Entry.Low) / xauPip
}

func unsweptRefusals(rec *telemetry.Recorder) int64 {
	_, counts := rec.Snapshot()
	var n int64
	for _, c := range counts {
		if c.Counter == string(telemetry.CounterStrategyKeyLevelUnsweptRefused) {
			n += c.Count
		}
	}
	return n
}

// The shipped XAU configuration (optional) publishes the v13 setups unchanged, plus unswept reactions at major levels, each with the stop beyond the band's far
// edge and inside the stop budget; required refuses those same reactions and counts them, exactly as v13.
func TestKL15OptionalPublishesUnsweptReactionsAtMajorLevels(t *testing.T) {
	recRequired, recOptional := telemetry.NewRecorder(), telemetry.NewRecorder()
	required := replayKL(t, loadFixture(t, majorFixture), "v13", recRequired, nil)
	optional := replayKL(t, loadFixture(t, majorFixture), "", recOptional, nil)
	if len(required) != 2 {
		t.Fatalf("required is v13: two setups, got %d", len(required))
	}
	for _, c := range required {
		if !evidenceHas(c, keylevel.ReactionSweptEvidence) || evidenceHas(c, keylevel.ReactionUnsweptEvidence) {
			t.Fatalf("required publishes swept reactions only, as v13: %v", c.Evidence)
		}
		if _, present := c.Quality.Components["kl_reaction_swept"]; present {
			t.Fatal("required carries no v14 measure (v13 output)")
		}
		o := findAt(optional, c.CreatedAt, c.Direction)
		if o == nil || o.ID != c.ID || o.Entry != c.Entry || o.Invalidation.Price != c.Invalidation.Price || !sweptOf(t, *o) {
			t.Fatalf("a swept setup publishes in both modes with the same entry and stop: %+v vs %+v", o, c)
		}
	}
	unswept := 0
	for _, c := range optional {
		if ruleSet(c) == "" {
			t.Fatalf("only major levels publish: %v", c.Evidence)
		}
		if err := c.Validate(); err != nil {
			t.Fatal(err)
		}
		if sweptOf(t, c) {
			continue
		}
		unswept++
		// the stop lies beyond the far edge of the reaction band (the published structure), and the stop budget (50-70 pips from the entry edge) holds
		if c.Direction == market.Buy && !(float64(c.Invalidation.Price) < c.Structure.Low) || c.Direction == market.Sell && !(float64(c.Invalidation.Price) > c.Structure.High) {
			t.Fatalf("an unswept stop is beyond the band's far edge: stop %v band %v-%v", c.Invalidation.Price, c.Structure.Low, c.Structure.High)
		}
		if p := stopPips(c); p < 50-1e-6 || p > 70+1e-6 {
			t.Fatalf("the stop budget applies: %.1f pips", p)
		}
		if evidenceHas(c, "invalidation_beyond_reaction_extreme") {
			t.Fatalf("an unswept reaction is not anchored to its own extreme: %v", c.Evidence)
		}
		if findAt(required, c.CreatedAt, c.Direction) != nil {
			t.Fatalf("required does not publish the unswept reaction at %d", c.CreatedAt)
		}
	}
	if unswept == 0 || len(optional) != len(required)+unswept {
		t.Fatalf("optional adds the unswept reactions: %d setups, %d unswept, %d required", len(optional), unswept, len(required))
	}
	if unsweptRefusals(recRequired) == 0 || unsweptRefusals(recOptional) != 0 {
		t.Fatalf("unswept refusals are counted only when they still happen: required %d, optional %d", unsweptRefusals(recRequired), unsweptRefusals(recOptional))
	}
}

// A minor level stays untraded whatever the sweep mode: on the 13 Nov bars every v12 setup is at a level v13 calls minor or that never reacted, so optional
// publishes only at major levels and counts the minor ones.
func TestKL15MinorLevelsStayRefused(t *testing.T) {
	rec := telemetry.NewRecorder()
	for _, c := range replayKL(t, loadFixture(t, xauFixture), "", rec, nil) {
		if ruleSet(c) == "" {
			t.Fatalf("a minor level published: %v", c.Evidence)
		}
	}
	_, counts := rec.Snapshot()
	var minor int64
	for _, c := range counts {
		if c.Counter == string(telemetry.CounterStrategyRefused) && c.Strategy == "key_level" && c.Reason == keylevel.RefusedMinorLevel {
			minor += c.Count
		}
	}
	if minor == 0 {
		t.Fatal("minor levels are counted minor_level")
	}
}

// FX keeps the swing model: major_sweep_mode is never read there, so the candidates carry neither the unswept code nor the v14 measure.
func TestKL15FXUnchanged(t *testing.T) {
	for _, c := range replayKL(t, loadFixture(t, gbpusdFixture), "", nil, nil) {
		if evidenceHas(c, keylevel.ReactionUnsweptEvidence) {
			t.Fatalf("FX: %v", c.Evidence)
		}
		if _, present := c.Quality.Components["kl_reaction_swept"]; present {
			t.Fatal("FX carries no v14 measure")
		}
	}
}

// BUY and SELL are mirrors under optional too: every setup reflects about 7600 with its entry, stop and swept flag.
func TestKL15MirroredBuyAndSell(t *testing.T) {
	mirrored := loadFixture(t, majorFixture)
	for tf, rows := range mirrored.Timeframes {
		out := make([][]float64, len(rows))
		for i, r := range rows {
			out[i] = []float64{r[0], 7600 - r[1], 7600 - r[3], 7600 - r[2], 7600 - r[4], r[5]}
		}
		mirrored.Timeframes[tf] = out
	}
	// Every original setup has its exact mirror. The mirror may carry ONE extra setup, and only one whose stop sits exactly on the 70-pip cap: the deep-entry cut
	// (strategyutil.StopBudget.Fit) rounds the cut edge to the tick with an epsilon on each side, so exactly at the cap one side publishes and the other does not
	// (17 Nov 07:15 BUY 3870.75-3874.73, stop 3867.73 publishes only mirrored). That floating-point edge is the shared budget's and pre-dates the sweep mode.
	orig := replayKL(t, loadFixture(t, majorFixture), "", nil, nil)
	mir := replayKL(t, mirrored, "", nil, nil)
	if len(orig) == 0 || len(mir) < len(orig) || len(mir) > len(orig)+1 {
		t.Fatalf("%d vs %d", len(orig), len(mir))
	}
	for _, b := range mir {
		opposite := market.Sell
		if b.Direction == market.Sell {
			opposite = market.Buy
		}
		if findAt(orig, b.CreatedAt, opposite) == nil {
			if p := stopPips(b); p < 70-0.05 || p > 70+0.05 {
				t.Fatalf("an extra mirrored setup off the cap at %d (%.2f pips)", b.CreatedAt, p)
			}
		}
	}
	for _, a := range orig {
		opposite := market.Sell
		if a.Direction == market.Sell {
			opposite = market.Buy
		}
		b := findAt(mir, a.CreatedAt, opposite)
		if b == nil {
			t.Fatalf("no mirror at %d", a.CreatedAt)
		}
		if !near(b.Entry.Low, 7600-a.Entry.High) || !near(b.Entry.High, 7600-a.Entry.Low) || !near(float64(b.Invalidation.Price), 7600-float64(a.Invalidation.Price)) ||
			sweptOf(t, a) != sweptOf(t, *b) {
			t.Fatalf("mirror %+v %v vs %+v %v", b.Entry, b.Invalidation.Price, a.Entry, a.Invalidation.Price)
		}
	}
}

// No look-ahead: cutting the bars at the 17 Nov 07:00 bar leaves every setup published before it unchanged.
func TestKL15TruncatedHistoryIsIdentical(t *testing.T) {
	cut := loadFixture(t, majorFixture)
	for tf, rows := range cut.Timeframes {
		var kept [][]float64
		for _, r := range rows {
			if int64(r[0]) < majorBuy2 {
				kept = append(kept, r)
			}
		}
		cut.Timeframes[tf] = kept
	}
	full := replayKL(t, loadFixture(t, majorFixture), "", nil, nil)
	part := replayKL(t, cut, "", nil, nil)
	before := 0
	for _, c := range full {
		if c.CreatedAt < majorBuy2 {
			before++
			p := findAt(part, c.CreatedAt, c.Direction)
			if p == nil || p.ID != c.ID || p.Entry != c.Entry || p.Invalidation.Price != c.Invalidation.Price {
				t.Fatalf("setup at %d changed by the cut", c.CreatedAt)
			}
		}
	}
	if before == 0 || len(part) != before {
		t.Fatalf("the cut keeps exactly the earlier setups: %d of %d", len(part), before)
	}
}
