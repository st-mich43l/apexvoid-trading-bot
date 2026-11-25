package replaycapture_test

// Key Level v13 (PR-KL13): on XAU Key Level walks its own major-level book (level_model: major); FX keeps level_model swing, which is v12 exactly. Fixtures are real
// production XAU bars:
//
//   - testdata/key_level_major_xau_2026-11-17.json (M5 from 15 Nov, M15 last 300, H1 last 450, closed at 17 Nov 07:15 UTC): v13 publishes two BUYs, 15 Nov 23:35
//     and 17 Nov 07:00, both off a session level (Asia/London lows) that is also a $10-handle swing and flip level; v12 published 43 setups on the same bars.
//   - testdata/key_level_xau_2026-11-13.json (the Q2 fixture): v13 publishes none of v12's 18 setups; TestKL13NamesEveryV12SetupItDrops says why for each.

import (
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/telemetry"
)

// replayKL replays Key Level alone with the given level_model ("" keeps the shipped configuration: major on XAU, swing on FX).
func replayKL(t *testing.T, capture *replaycapture.Capture, model string, rec *telemetry.Recorder, hook func(engine.Evaluation)) []opportunity.Candidate {
	t.Helper()
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	var off []string
	for _, id := range strategy.KnownIDs() {
		if id != "key_level" {
			off = append(off, string(id))
		}
	}
	opts := replaycapture.Options{DisabledStrategies: off, Telemetry: rec, OnEvaluation: hook}
	if model != "" {
		opts.StrategyParameters = map[string]map[string]any{"key_level": {"level_model": model}}
	}
	result, err := replaycapture.Replay(doc, capture, market.M5, opts)
	if err != nil {
		t.Fatal(err)
	}
	var out []opportunity.Candidate
	for _, c := range result.Discovered {
		if c.Strategy == "key_level" {
			out = append(out, c)
		}
	}
	return out
}

// xauKeyLevelParams are the XAU key_level parameters as the engine builds them (with the instrument overrides).
func xauKeyLevelParams(t *testing.T) map[string]any {
	t.Helper()
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, "XAU"); err != nil {
		t.Fatal(err)
	}
	for _, s := range settings.Strategies {
		if s.ID == "key_level" {
			return s.Parameters
		}
	}
	t.Fatal("key_level not configured")
	return nil
}

func bookAt(t *testing.T, ev engine.Evaluation, params map[string]any) []keylevel.BookLevel {
	frames := map[market.Timeframe]*strategyutil.LegacyDetector{}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		if d, ok := strategyutil.NewLegacyDetectorForFrame(ev.Context, tf, strategyutil.LegacyDetectorSettings{}); ok {
			frames[tf] = d
		}
	}
	m5 := frames[market.M5]
	book, err := keylevel.StudyBook(params, frames, m5.Price, ev.BarTime+300)
	if err != nil {
		t.Fatal(err)
	}
	return book
}

const (
	majorFixture       = "testdata/key_level_major_xau_2026-11-17.json"
	majorBuy1    int64 = 1794785700 // 15 Nov 23:35 UTC
	majorBuy2    int64 = 1794898800 // 17 Nov 07:00 UTC
)

func ruleSet(c opportunity.Candidate) string {
	var rules []string
	for _, code := range []string{"kl_major_session", "kl_major_round", "kl_major_swing", "kl_major_flip"} {
		if evidenceHas(c, code) {
			rules = append(rules, strings.TrimPrefix(code, "kl_major_"))
		}
	}
	return strings.Join(rules, "+")
}

// v13 publishes only at major levels, with the facts that made them major; the entry, the stop and the structure are those of the v12 steps on the major band.
func TestKL13PublishesOnlyAtMajorLevels(t *testing.T) {
	list := replayKL(t, loadFixture(t, majorFixture), "", nil, nil)
	if len(list) != 2 {
		t.Fatalf("two major-level setups on these bars, got %d", len(list))
	}
	for _, c := range list {
		if c.StrategyVersion != "v13" || c.Quality.Components["kl_strength_score"] <= 0 || ruleSet(c) == "" || !evidenceHas(c, "kl_level_source_session") {
			t.Fatalf("a v13 candidate carries its major rules, source and strength: %s %v %v", c.StrategyVersion, ruleSet(c), c.Quality.Components["kl_strength_score"])
		}
		if c.Structure == nil || c.Structure.Timeframe != market.H1 || c.StructureTimeframe != market.H1 {
			t.Fatalf("a session level's structure timeframe is H1: %+v", c.Structure)
		}
		if err := c.Validate(); err != nil {
			t.Fatal(err)
		}
	}
	b := findAt(list, majorBuy2, market.Buy)
	if b == nil || !near(b.Entry.Low, 3871.26) || !near(b.Entry.High, 3875.56) || !near(float64(b.Invalidation.Price), 3868.56) {
		t.Fatalf("the 17 Nov 07:00 BUY: %+v", b)
	}
	if findAt(list, majorBuy1, market.Buy) == nil {
		t.Fatal("the 15 Nov 23:35 BUY")
	}
}

// The same bars under level_model swing are v12: 43 setups (the rollback is the deletion of the XAU override).
func TestKL13SwingModelIsV12OnXAU(t *testing.T) {
	v12 := replayKL(t, loadFixture(t, majorFixture), "swing", nil, nil)
	if len(v12) != 43 {
		t.Fatalf("level_model swing publishes the v12 setups: %d", len(v12))
	}
	for _, c := range v12 {
		if evidenceHas(c, "kl_level_source_session") || evidenceHas(c, "kl_level_source_round") || c.Quality.Components["kl_strength_score"] != 0 {
			t.Fatalf("a swing-model candidate carries no major facts: %+v", c.Evidence)
		}
	}
}

// A minor level with a swept M5 reaction is refused: on the 13 Nov bars v12 published a swept BUY off an M15 swing level (one reaction, no span) at 08:20; v13
// counts it minor_level and publishes nothing there.
func TestKL13RefusesASweptReactionAtAMinorLevel(t *testing.T) {
	const at int64 = 1794459600
	v12 := replayKL(t, loadFixture(t, xauFixture), "swing", nil, nil)
	swept := findAt(v12, at, market.Buy)
	if swept == nil || !evidenceHas(*swept, keylevel.ReactionSweptEvidence) {
		t.Fatalf("v12 published the swept reaction: %+v", swept)
	}
	rec := telemetry.NewRecorder()
	if c := findAt(replayKL(t, loadFixture(t, xauFixture), "", rec, nil), at, market.Buy); c != nil {
		t.Fatalf("v13 must not publish at a minor level: %+v", c.Entry)
	}
	_, counts := rec.Snapshot()
	found := false
	for _, c := range counts {
		found = found || c.Counter == string(telemetry.CounterStrategyRefused) && c.Strategy == "key_level" && c.Reason == keylevel.RefusedMinorLevel
	}
	if !found {
		t.Fatal("the minor level is counted")
	}
}

// Regression: every v12 setup of the 13 Nov fixture, the book level its reaction band overlaps as of that bar, and why v13 does not publish it.
func TestKL13NamesEveryV12SetupItDrops(t *testing.T) {
	why := map[int64]string{
		1794455100: "major S",   // PDL (H1 band 3979.12-3986.16): major, but no M5 reaction off that band passed the v12 steps on this bar
		1794459600: "minor",     // M15 swing cluster at 3974.01: one reaction, no span (W needs 2 over 8 h)
		1794508500: "minor",     // LONDON_L 3884.97: broken (an accepted H1 break since its last reaction), so not intact
		1794510000: "minor",     // M5 swing 3878.40 (no reaction) and the broken LONDON_L
		1794513600: "minor",     // the broken LONDON_L
		1794515400: "minor",     // the broken LONDON_L
		1794516600: "minor",     // M15 swing 3875.66, M5 swing 3878.40 (no reactions) and the broken LONDON_L
		1794527100: "minor",     // the broken LONDON_L ($10 handle inside)
		1794529200: "minor",     // idem
		1794530100: "minor",     // idem
		1794531000: "minor",     // idem
		1794543300: "minor",     // M15 swing 3916.70, no reaction
		1794546300: "minor",     // idem
		1794547800: "minor",     // idem
		1794558600: "minor",     // M5 swing 3892.06, no reaction
		1794564900: "minor",     // the $3900 handle: one reaction, then broken
		1794566700: "major F+S", // LONDON_L/ASIA_L (S, F: 2 reactions over 17 h): major, but no SELL reaction off its H1 band passed the v12 steps on this bar
		1794569100: "major F+S", // idem (the v11 loser of Q2)
	}
	params := xauKeyLevelParams(t)
	v12 := replayKL(t, loadFixture(t, xauFixture), "swing", nil, nil)
	if len(v12) != len(why) {
		t.Fatalf("v12 published %d setups on the fixture, the table names %d", len(v12), len(why))
	}
	at := map[int64][]opportunity.Candidate{}
	for _, c := range v12 {
		at[c.CreatedAt] = append(at[c.CreatedAt], c)
	}
	seen := 0
	v13 := replayKL(t, loadFixture(t, xauFixture), "", nil, func(ev engine.Evaluation) {
		for _, c := range at[ev.BarTime] {
			seen++
			class, rules := "minor", map[string]bool{}
			for _, l := range bookAt(t, ev, params) {
				if l.Price+l.Band < c.Structure.Low || l.Price-l.Band > c.Structure.High || !l.Major {
					continue
				}
				class = "major"
				for _, r := range l.Rules {
					rules[map[string]string{"S": "S", "R": "R", "W": "W", "F": "F"}[r]] = true
				}
			}
			got := class
			if class == "major" {
				var list []string
				for _, r := range []string{"F", "R", "S", "W"} {
					if rules[r] {
						list = append(list, r)
					}
				}
				got += " " + strings.Join(list, "+")
			}
			if got != why[c.CreatedAt] {
				t.Errorf("%d %s: the book says %q, the table %q", c.CreatedAt, c.Direction, got, why[c.CreatedAt])
			}
		}
	})
	if seen != len(why) {
		t.Fatalf("checked %d of %d setups", seen, len(why))
	}
	if len(v13) != 0 {
		t.Fatalf("v13 publishes none of them: %d", len(v13))
	}
}

// No look-ahead: cutting the bars before the 17 Nov 07:00 bar removes that setup and nothing published earlier changes.
func TestKL13HasNoLookAhead(t *testing.T) {
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
	if findAt(part, majorBuy2, market.Buy) != nil || findAt(full, majorBuy2, market.Buy) == nil {
		t.Fatal("the setup appears only once its confirming bar has closed")
	}
	if len(part) != 1 || part[0].ID != findAt(full, majorBuy1, market.Buy).ID {
		t.Fatalf("the earlier setup is unchanged by the cut: %d", len(part))
	}
}

// BUY and SELL are mirrors: the bars reflected about 7600 (a $100 handle, so the round grid reflects onto itself; PDH and PDL swap) publish the two setups as SELLs
// with the entry, the stop and the structure reflected exactly.
func TestKL13MirroredBuyAndSell(t *testing.T) {
	mirrored := loadFixture(t, majorFixture)
	for tf, rows := range mirrored.Timeframes {
		out := make([][]float64, len(rows))
		for i, r := range rows {
			out[i] = []float64{r[0], 7600 - r[1], 7600 - r[3], 7600 - r[2], 7600 - r[4], r[5]}
		}
		mirrored.Timeframes[tf] = out
	}
	orig := replayKL(t, loadFixture(t, majorFixture), "", nil, nil)
	mir := replayKL(t, mirrored, "", nil, nil)
	for _, at := range []int64{majorBuy1, majorBuy2} {
		a, b := findAt(orig, at, market.Buy), findAt(mir, at, market.Sell)
		if a == nil || b == nil {
			t.Fatalf("both directions at %d: %v %v", at, a != nil, b != nil)
		}
		if !near(b.Entry.Low, 7600-a.Entry.High) || !near(b.Entry.High, 7600-a.Entry.Low) || !near(float64(b.Invalidation.Price), 7600-float64(a.Invalidation.Price)) ||
			!near(b.Structure.High, 7600-a.Structure.Low) || ruleSet(*a) != ruleSet(*b) {
			t.Fatalf("mirror %+v %v %+v vs %+v %v %+v", b.Entry, b.Invalidation.Price, b.Structure, a.Entry, a.Invalidation.Price, a.Structure)
		}
	}
}

// FX keeps level_model swing: the shipped configuration and an explicit swing give the same GBPUSD candidates (FX byte-identity against v12 is proven by replay).
func TestKL13FXIsTheSwingModel(t *testing.T) {
	shipped := replayKL(t, loadFixture(t, gbpusdFixture), "", nil, nil)
	swing := replayKL(t, loadFixture(t, gbpusdFixture), "swing", nil, nil)
	if len(shipped) == 0 || len(shipped) != len(swing) {
		t.Fatalf("GBPUSD %d vs %d", len(shipped), len(swing))
	}
	for i := range shipped {
		if shipped[i].ID != swing[i].ID || evidenceHas(shipped[i], "kl_level_source_session") {
			t.Fatalf("FX is the swing model: %s vs %s", shipped[i].ID, swing[i].ID)
		}
	}
}
