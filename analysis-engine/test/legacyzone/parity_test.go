package legacyzone_test

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/legacyzone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

type goldenZone struct {
	B, T    float64
	Side    string
	Origin  int
	Touches int
	Mit     bool
	Src     string
	Srcs    []string
	BK      string
	BI      int
}

type goldenCase struct {
	Symbol string      `json:"symbol"`
	Bars   [][]float64 `json:"bars"`
	Swings []struct {
		I  int
		K  string
		P  float64
		L  string
		CI int
	} `json:"swings"`
	Structure string `json:"structure"`
	Breaks    []struct {
		Kind  string
		Dir   string
		Level float64
		I     int
	} `json:"breaks"`
	Levels []struct {
		P float64
		K string
		T int
		B float64
		S float64
	} `json:"levels"`
	Legs []struct {
		S, E int
		D    string
		Z    float64
	} `json:"legs"`
	SD     []goldenZone `json:"sd"`
	OB     []goldenZone `json:"ob"`
	Flip   []goldenZone `json:"flip"`
	FVG    []goldenZone `json:"fvg"`
	Merged []goldenZone `json:"merged"`
	Single []goldenZone `json:"single"`
}

type goldenParams struct {
	ATRLength               int     `json:"atr_length"`
	SwingFractalN           int     `json:"swing_fractal_n"`
	ZigzagPct               float64 `json:"zigzag_pct"`
	ZigzagATRMult           float64 `json:"zigzag_atr_mult"`
	DisplacementATRMult     float64 `json:"displacement_atr_mult"`
	MomentumBodyFrac        float64 `json:"momentum_body_frac"`
	ZoneWidth               string  `json:"zone_width"`
	LevelClusterATR         float64 `json:"level_cluster_atr"`
	RoundStep               float64 `json:"round_step"`
	KeyLevelMinTouches      int     `json:"key_level_min_touches"`
	MaxClusterSpanMultiple  float64 `json:"max_cluster_span_multiple"`
	BreakoutAcceptBars      int     `json:"breakout_accept_bars"`
	FlipZoneAcceptBars      *int    `json:"flip_zone_accept_bars"`
	FlipZoneMaxBreakAgeBars *int    `json:"flip_zone_max_break_age_bars"`
	FlipBandBodyFraction    float64 `json:"flip_band_body_fraction"`
	ZoneMergeOverlap        float64 `json:"zone_merge_overlap"`
	MaxMergedZoneATR        float64 `json:"max_merged_zone_atr"`
	CausalStructure         bool    `json:"causal_structure"`
}

func near(a, b float64) bool { return math.Abs(a-b) <= 1e-9*math.Max(1, math.Abs(b)) }

func compareZones(t *testing.T, name string, ci int, got []legacyzone.Zone, want []goldenZone) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("case %d %s: %d zones, python %d", ci, name, len(got), len(want))
	}
	for i := range got {
		g, w := got[i], want[i]
		srcsEqual := len(g.Sources) == len(w.Srcs)
		for j := 0; srcsEqual && j < len(g.Sources); j++ {
			srcsEqual = g.Sources[j] == w.Srcs[j]
		}
		if !near(g.Bottom, w.B) || !near(g.Top, w.T) || g.Side != w.Side || g.OriginIndex != w.Origin || g.Touches != w.Touches ||
			g.Mitigated != w.Mit || g.Source != w.Src || !srcsEqual || g.BreakKind != w.BK || g.BreakIndex != w.BI {
			t.Fatalf("case %d %s[%d]:\n  go     %+v\n  python %+v", ci, name, i, g, w)
		}
	}
}

// TestZoneChainMatchesThePythonReferenceOnProductionBars replays the legacy
// Python zone chain (swings -> breaks -> levels -> displacement legs -> supply/
// demand, order blocks, breakers, flip zones, FVGs -> merge -> mitigation) over
// 24 windows of 300 real XAU M5 bars with the production detector settings and
// requires every intermediate and final list to match.
func TestZoneChainMatchesThePythonReferenceOnProductionBars(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "legacyzone-parity.json"))
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Params map[string]goldenParams `json:"params"`
		Cases  []goldenCase            `json:"cases"`
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) < 20 {
		t.Fatalf("golden unexpectedly small: %d", len(golden.Cases))
	}
	for ci, c := range golden.Cases {
		p := golden.Params[c.Symbol]
		bars := make([]market.Candle, len(c.Bars))
		for i, b := range c.Bars {
			bars[i] = market.Candle{Time: int64(b[0]), Open: b[1], High: b[2], Low: b[3], Close: b[4]}
		}
		atr := legacyzone.ATRSeries(bars, p.ATRLength)
		asOf := -1
		if p.CausalStructure {
			asOf = len(bars) - 1
		}
		swings := legacyzone.FindSwings(bars, p.SwingFractalN, p.ZigzagPct, p.ZigzagATRMult, atr, asOf)
		if len(swings) != len(c.Swings) {
			t.Fatalf("case %d swings: %d vs python %d", ci, len(swings), len(c.Swings))
		}
		for i, s := range swings {
			w := c.Swings[i]
			if s.Index != w.I || s.Kind != w.K || !near(s.Price, w.P) || s.Label != w.L || s.ConfirmedIndex != w.CI {
				t.Fatalf("case %d swing[%d]: go %+v python %+v", ci, i, s, w)
			}
		}
		if got := legacyzone.MarketStructure(swings); got != c.Structure {
			t.Fatalf("case %d structure %s vs %s", ci, got, c.Structure)
		}
		breaks := legacyzone.StructureBreaks(swings, bars, p.CausalStructure, p.SwingFractalN)
		if len(breaks) != len(c.Breaks) {
			t.Fatalf("case %d breaks: %d vs %d", ci, len(breaks), len(c.Breaks))
		}
		for i, b := range breaks {
			w := c.Breaks[i]
			if b.Kind != w.Kind || b.Direction != w.Dir || !near(b.Level, w.Level) || b.Index != w.I {
				t.Fatalf("case %d break[%d]: go %+v python %+v", ci, i, b, w)
			}
		}
		levels := legacyzone.KeyLevels(swings, atr, p.LevelClusterATR, p.RoundStep, p.KeyLevelMinTouches, p.MaxClusterSpanMultiple, bars)
		if len(levels) != len(c.Levels) {
			t.Fatalf("case %d levels: %d vs %d", ci, len(levels), len(c.Levels))
		}
		for i, l := range levels {
			w := c.Levels[i]
			if !near(l.Price, w.P) || l.Kind != w.K || l.Touches != w.T || !near(l.Band, w.B) || !near(l.Strength, w.S) {
				t.Fatalf("case %d level[%d]: go %+v python %+v", ci, i, l, w)
			}
		}
		legs := legacyzone.Displacement(bars, atr, p.DisplacementATRMult, p.MomentumBodyFrac)
		if len(legs) != len(c.Legs) {
			t.Fatalf("case %d legs: %d vs %d", ci, len(legs), len(c.Legs))
		}
		for i, g := range legs {
			w := c.Legs[i]
			if g.Start != w.S || g.End != w.E || g.Direction != w.D || !near(g.Size, w.Z) {
				t.Fatalf("case %d leg[%d]: go %+v python %+v", ci, i, g, w)
			}
		}
		sd := legacyzone.BreakerBlocks(legacyzone.SupplyDemand(bars, legs), bars)
		ob := legacyzone.BreakerBlocks(legacyzone.OrderBlocks(bars, legs, breaks, p.ZoneWidth), bars)
		accept := p.BreakoutAcceptBars
		if p.FlipZoneAcceptBars != nil {
			accept = *p.FlipZoneAcceptBars
		}
		maxAge := -1
		if p.FlipZoneMaxBreakAgeBars != nil {
			maxAge = *p.FlipZoneMaxBreakAgeBars
		}
		flip := legacyzone.FlipZones(levels, breaks, bars, accept, maxAge, p.FlipBandBodyFraction)
		fvg := legacyzone.FVG(bars)
		compareZones(t, "sd", ci, sd, c.SD)
		compareZones(t, "ob", ci, ob, c.OB)
		compareZones(t, "flip", ci, flip, c.Flip)
		compareZones(t, "fvg", ci, fvg, c.FVG)

		all := append(append(append(append([]legacyzone.Zone(nil), sd...), ob...), flip...), fvg...)
		merged := legacyzone.MergeZones(all, p.ZoneMergeOverlap, legacyzone.ATRScalar(atr, 1)*math.Max(0, p.MaxMergedZoneATR))
		merged = legacyzone.MarkMitigation(merged, bars, maxInt(0, len(bars)-1))
		compareZones(t, "merged", ci, merged, c.Merged)
		single := legacyzone.MarkMitigation(legacyzone.AsSingleZones(append(append(append([]legacyzone.Zone(nil), sd...), ob...), fvg...)), bars, maxInt(0, len(bars)-1))
		compareZones(t, "single", ci, single, c.Single)
	}
	t.Log(fmt.Sprintf("%d windows: swings, breaks, levels, legs, sd, ob, flip, fvg, merged and single zones all identical to Python", len(golden.Cases)))
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
