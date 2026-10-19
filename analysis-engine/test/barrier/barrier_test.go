package barrier_test

import (
	"encoding/json"
	"math"
	"os"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/barrier"
)

type goldenZone struct {
	Timeframe string  `json:"timeframe"`
	Side      string  `json:"side"`
	Low       float64 `json:"low"`
	High      float64 `json:"high"`
	ATR       float64 `json:"atr"`
	Score     float64 `json:"score"`
	Touches   int     `json:"touches"`
	Live      bool    `json:"live"`
}

type goldenCase struct {
	Name   string `json:"name"`
	Config struct {
		PipSize      float64 `json:"pip_size"`
		MaxWidthATR  float64 `json:"max_width_atr"`
		MaxWidthPips float64 `json:"max_width_pips"`
	} `json:"config"`
	Zones    []goldenZone      `json:"zones"`
	Expected []barrier.Barrier `json:"expected"`
}

// TestBuildMatchesPythonStructuralBarrierBook replays 300 randomized cases
// (touching bands, zero-width and oversized zones, cross-side conflicts, dead
// zones, out-of-scope timeframes, missing ATR) whose expected output was
// produced by the real Python zone_meets_execution_width and
// canonicalize_structural_barriers. Any drift from the Python implementation
// fails here.
func TestBuildMatchesPythonStructuralBarrierBook(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_parity_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []goldenCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) == 0 {
		t.Fatal("empty golden")
	}
	nonEmpty := 0
	for _, c := range golden.Cases {
		zones := make([]barrier.Zone, len(c.Zones))
		for i, z := range c.Zones {
			zones[i] = barrier.Zone{
				Timeframe: z.Timeframe, Side: barrier.Side(z.Side), Low: z.Low, High: z.High,
				ATR: z.ATR, Score: z.Score, Touches: z.Touches, Live: z.Live,
			}
		}
		got := barrier.Build(zones, barrier.Config{
			PipSize: c.Config.PipSize, MaxWidthATR: c.Config.MaxWidthATR, MaxWidthPips: c.Config.MaxWidthPips,
		})
		if len(got) != len(c.Expected) {
			t.Fatalf("%s: got %d barriers, python produced %d\n got=%+v\nwant=%+v", c.Name, len(got), len(c.Expected), got, c.Expected)
		}
		if len(got) > 0 {
			nonEmpty++
		}
		for i := range got {
			g, w := got[i], c.Expected[i]
			if g.Side != w.Side || g.Tier != w.Tier || g.Touches != w.Touches ||
				math.Abs(g.Low-w.Low) > 1e-12 || math.Abs(g.High-w.High) > 1e-12 || math.Abs(g.Score-w.Score) > 1e-12 ||
				len(g.SourceTimeframes) != len(w.SourceTimeframes) {
				t.Fatalf("%s: barrier %d differs\n got=%+v\nwant=%+v", c.Name, i, g, w)
			}
			for k := range g.SourceTimeframes {
				if g.SourceTimeframes[k] != w.SourceTimeframes[k] {
					t.Fatalf("%s: barrier %d source timeframes differ: got %v want %v", c.Name, i, g.SourceTimeframes, w.SourceTimeframes)
				}
			}
		}
	}
	if nonEmpty < 100 {
		t.Fatalf("golden exercises too little: only %d non-empty cases", nonEmpty)
	}
}

func TestBuildDropsZeroWidthOversizedAndOutOfScopeZones(t *testing.T) {
	cfg := barrier.Config{PipSize: 0.0001, MaxWidthATR: 2, MaxWidthPips: 100}
	zones := []barrier.Zone{
		{Timeframe: "M5", Side: barrier.Sell, Low: 1.1350, High: 1.1350, ATR: 0.0002, Live: true},  // zero width
		{Timeframe: "M5", Side: barrier.Sell, Low: 1.1350, High: 1.1400, ATR: 0.0002, Live: true},  // > 2 ATR
		{Timeframe: "M1", Side: barrier.Sell, Low: 1.1350, High: 1.13502, ATR: 0.0001, Live: true}, // M1 out of scope
		{Timeframe: "M15", Side: barrier.Sell, Low: 1.1350, High: 1.13508, ATR: 0.0002, Live: false},
		{Timeframe: "H1", Side: barrier.Sell, Low: 1.1350, High: 1.13508, ATR: 0, Live: true}, // no ATR
		{Timeframe: "M15", Side: barrier.Sell, Low: 1.1350, High: 1.13508, ATR: 0.0002, Live: true},
	}
	got := barrier.Build(zones, cfg)
	if len(got) != 1 || got[0].SourceTimeframes[0] != "M15" {
		t.Fatalf("only the live in-scope in-width M15 zone should survive: %+v", got)
	}
}

func TestBuildMergesTouchingSameSideBandsAcrossTimeframes(t *testing.T) {
	cfg := barrier.Config{PipSize: 0.0001, MaxWidthATR: 6, MaxWidthPips: 100}
	got := barrier.Build([]barrier.Zone{
		{Timeframe: "H1", Side: barrier.Buy, Low: 1.1300, High: 1.1310, ATR: 0.0010, Score: 0.4, Touches: 2, Live: true},
		{Timeframe: "M5", Side: barrier.Buy, Low: 1.1310, High: 1.1315, ATR: 0.0003, Score: 0.9, Touches: 1, Live: true},
	}, cfg)
	if len(got) != 1 || got[0].Low != 1.1300 || got[0].High != 1.1315 || got[0].Score != 0.9 || got[0].Touches != 1 {
		t.Fatalf("touching same-side bands must merge to the union with max score and min touches: %+v", got)
	}
	if want := []string{"M5", "H1"}; len(got[0].SourceTimeframes) != 2 || got[0].SourceTimeframes[0] != want[0] || got[0].SourceTimeframes[1] != want[1] {
		t.Fatalf("source timeframes must be ordered by timeframe length: %v", got[0].SourceTimeframes)
	}
}

func provenanceZones() []barrier.Zone {
	touched := int64(1700000900)
	return []barrier.Zone{
		{Timeframe: "H1", Side: barrier.Sell, Low: 2025, High: 2030, ATR: 8, Score: 0.95, Touches: 1, Live: true, ID: "zone:H1:supply:b", State: "touched", OriginTime: 1699990000, CreatedAt: 1699990600, LastTouchedAt: &touched},
		{Timeframe: "M15", Side: barrier.Sell, Low: 2020, High: 2025, ATR: 4, Score: 0.8, Touches: 2, Live: true, ID: "zone:M15:supply:a", State: "partially_mitigated", OriginTime: 1699980000, CreatedAt: 1699980900},
		{Timeframe: "M5", Side: barrier.Buy, Low: 1990, High: 1993, ATR: 2, Score: 0.5, Touches: 0, Live: true, ID: "zone:M5:demand:c", State: "fresh", OriginTime: 1700000000, CreatedAt: 1700000300},
	}
}

// A merged barrier names every zone behind it, each with its own band, state, touches, strength and times, in merge order
// (ascending low), and publishes only what the engine knew.
func TestBuildPublishesTheOriginalZonesBehindAMergedBarrier(t *testing.T) {
	cfg := barrier.Config{PipSize: 0.1, MaxWidthATR: 2, MaxWidthPips: 500}
	got := barrier.Build(provenanceZones(), cfg)
	if len(got) != 2 {
		t.Fatalf("want one buy and one sell barrier: %+v", got)
	}
	var sell barrier.Barrier
	for _, b := range got {
		if b.Side == barrier.Sell {
			sell = b
		}
	}
	if len(sell.Members) != 2 || sell.Members[0].ID != "zone:M15:supply:a" || sell.Members[1].ID != "zone:H1:supply:b" {
		t.Fatalf("members must follow the merge order (ascending low): %+v", sell.Members)
	}
	m := sell.Members[0]
	if m.Timeframe != "M15" || m.Low != 2020 || m.High != 2025 || m.State != "partially_mitigated" || m.Touches != 2 || m.Strength != 0.8 ||
		m.OriginTime != 1699980000 || m.CreatedAt != 1699980900 || m.LastTouchedAt != nil {
		t.Fatalf("member must carry the zone's own facts and no invented last touch: %+v", m)
	}
	if h := sell.Members[1]; h.LastTouchedAt == nil || *h.LastTouchedAt != 1700000900 {
		t.Fatalf("a known last touch is published: %+v", h)
	}
	raw, err := json.Marshal(sell.Members[0])
	if err != nil {
		t.Fatal(err)
	}
	if string(raw) != `{"id":"zone:M15:supply:a","timeframe":"M15","low":2020,"high":2025,"state":"partially_mitigated","touches":2,"strength":0.8,"origin_time":1699980000,"created_at":1699980900}` {
		t.Fatalf("unexpected member JSON (an unknown last touch must be absent, not null or 0): %s", raw)
	}
}

// Provenance describes the zones; it never selects, orders or merges them. The same zones with every provenance field blanked
// must build the same bands, tiers, scores, touches, timeframes and order.
func TestProvenanceDoesNotChangeTheBarrierBook(t *testing.T) {
	cfg := barrier.Config{PipSize: 0.1, MaxWidthATR: 2, MaxWidthPips: 500}
	with := barrier.Build(provenanceZones(), cfg)
	blank := provenanceZones()
	for i := range blank {
		blank[i].ID, blank[i].State, blank[i].OriginTime, blank[i].CreatedAt, blank[i].LastTouchedAt = "", "", 0, 0, nil
	}
	without := barrier.Build(blank, cfg)
	if len(with) != len(without) {
		t.Fatalf("provenance changed the number of barriers: %d vs %d", len(with), len(without))
	}
	for i := range with {
		a, b := with[i], without[i]
		if a.Side != b.Side || a.Low != b.Low || a.High != b.High || a.Tier != b.Tier || a.Score != b.Score || a.Touches != b.Touches ||
			len(a.SourceTimeframes) != len(b.SourceTimeframes) || len(a.Members) != len(b.Members) {
			t.Fatalf("barrier %d differs with provenance: %+v vs %+v", i, a, b)
		}
		for k := range a.Members {
			if a.Members[k].Low != b.Members[k].Low || a.Members[k].High != b.Members[k].High || a.Members[k].Touches != b.Members[k].Touches {
				t.Fatalf("barrier %d member %d differs: %+v vs %+v", i, k, a.Members[k], b.Members[k])
			}
		}
	}
	for i := 0; i < 20; i++ {
		again := barrier.Build(provenanceZones(), cfg)
		if a, b := mustJSON(t, with), mustJSON(t, again); a != b {
			t.Fatalf("the book is not deterministic:\n%s\n%s", a, b)
		}
	}
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}
