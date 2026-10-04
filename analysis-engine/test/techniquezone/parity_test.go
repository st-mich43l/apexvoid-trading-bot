package techniquezone_test

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
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

type goldenInstance struct {
	Tech, Side  string
	Low, High   float64
	Origin      int
	Touches     int
	Mit         bool
	Body        float64
	Clip        bool
	Slow, Shigh float64
	Srcs        []string
}

type goldenCase struct {
	Symbol string `json:"symbol"`
	// Start/End slice the committed XAU capture's M5 bars (the window).
	Start     int              `json:"start"`
	End       int              `json:"end"`
	Instances []goldenInstance `json:"instances"`
	Structure string           `json:"structure"` // set only on full cases
	Swings    []struct {
		I  int
		K  string
		P  float64
		L  string
		CI int
	} `json:"swings"`
	Breaks []struct {
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
	TG                      struct {
		MomentumBodyFrac         float64 `json:"momentum_body_frac"`
		PipSize                  float64 `json:"pip_size"`
		FVGEntryMaxWidthPrice    float64 `json:"fvg_entry_max_width_price"`
		FVGMaxATR                float64 `json:"fvg_max_atr"`
		RetestMaxTouches         int     `json:"technique_retest_max_touches"`
		InvalidationToleranceATR float64 `json:"technique_invalidation_tolerance_atr"`
		SweepReclaimBars         int     `json:"technique_sweep_reclaim_bars"`
		MaxBreakEpisodes         int     `json:"technique_max_break_episodes"`
	} `json:"tg"`
}

func near(a, b float64) bool { return math.Abs(a-b) <= 1e-9*math.Max(1, math.Abs(b)) }

func compareZones(t *testing.T, name string, ci int, got []techniquezone.Zone, want []goldenZone) {
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

func loadGolden(t *testing.T) (golden struct {
	Params map[string]goldenParams `json:"params"`
	Cases  []goldenCase            `json:"cases"`
}, all []market.Candle) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "techniquezone-parity.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	capRaw, err := os.ReadFile(filepath.Join("..", "..", "testdata", "replay-xau-production-capture-20260921.json"))
	if err != nil {
		t.Fatal(err)
	}
	var capture struct {
		Timeframes map[string][][]float64 `json:"timeframes"`
	}
	if err := json.Unmarshal(capRaw, &capture); err != nil {
		t.Fatal(err)
	}
	all = make([]market.Candle, len(capture.Timeframes["M5"]))
	for i, b := range capture.Timeframes["M5"] {
		all[i] = market.Candle{Time: int64(b[0]), Open: b[1], High: b[2], Low: b[3], Close: b[4], Volume: b[5]}
	}
	return golden, all
}

func chain(bars []market.Candle, p goldenParams) (swings []techniquezone.Swing, breaks []techniquezone.Break, levels []techniquezone.Level, legs []techniquezone.Leg, sd, ob, flip, fvg []techniquezone.Zone, atr []float64) {
	atr = techniquezone.ATRSeries(bars, p.ATRLength)
	asOf := -1
	if p.CausalStructure {
		asOf = len(bars) - 1
	}
	swings = techniquezone.FindSwings(bars, p.SwingFractalN, p.ZigzagPct, p.ZigzagATRMult, atr, asOf)
	breaks = techniquezone.StructureBreaks(swings, bars, p.CausalStructure, p.SwingFractalN)
	levels = techniquezone.KeyLevels(swings, atr, p.LevelClusterATR, p.RoundStep, p.KeyLevelMinTouches, p.MaxClusterSpanMultiple, bars)
	legs = techniquezone.Displacement(bars, atr, p.DisplacementATRMult, p.MomentumBodyFrac)
	sd = techniquezone.BreakerBlocks(techniquezone.SupplyDemand(bars, legs), bars)
	ob = techniquezone.BreakerBlocks(techniquezone.OrderBlocks(bars, legs, breaks, p.ZoneWidth), bars)
	accept := p.BreakoutAcceptBars
	if p.FlipZoneAcceptBars != nil {
		accept = *p.FlipZoneAcceptBars
	}
	maxAge := -1
	if p.FlipZoneMaxBreakAgeBars != nil {
		maxAge = *p.FlipZoneMaxBreakAgeBars
	}
	flip = techniquezone.FlipZones(levels, breaks, bars, accept, maxAge, p.FlipBandBodyFraction)
	fvg = techniquezone.FVG(bars)
	return
}

// TestZoneChainMatchesThePythonReferenceOnProductionBars replays the legacy
// Python zone chain (swings -> breaks -> levels -> displacement legs -> supply/
// demand, order blocks, breakers, flip zones, FVGs -> merge -> mitigation) over
// 120 windows of 300 real XAU M5 bars with the production detector settings.
// The first 24 windows compare every intermediate and final list; all 120
// compare the technique instances (what a detector may actually publish).
func TestZoneChainMatchesThePythonReferenceOnProductionBars(t *testing.T) {
	golden, all := loadGolden(t)
	if len(golden.Cases) < 100 {
		t.Fatalf("golden unexpectedly small: %d", len(golden.Cases))
	}
	full, instanceTotal := 0, 0
	for ci, c := range golden.Cases {
		p := golden.Params[c.Symbol]
		bars := all[c.Start:c.End]
		swings, breaks, levels, legs, sd, ob, flip, fvg, atr := chain(bars, p)

		if c.Structure != "" {
			full++
			if len(swings) != len(c.Swings) {
				t.Fatalf("case %d swings: %d vs python %d", ci, len(swings), len(c.Swings))
			}
			for i, s := range swings {
				w := c.Swings[i]
				if s.Index != w.I || s.Kind != w.K || !near(s.Price, w.P) || s.Label != w.L || s.ConfirmedIndex != w.CI {
					t.Fatalf("case %d swing[%d]: go %+v python %+v", ci, i, s, w)
				}
			}
			if got := techniquezone.MarketStructure(swings); got != c.Structure {
				t.Fatalf("case %d structure %s vs %s", ci, got, c.Structure)
			}
			if len(breaks) != len(c.Breaks) {
				t.Fatalf("case %d breaks: %d vs %d", ci, len(breaks), len(c.Breaks))
			}
			for i, b := range breaks {
				w := c.Breaks[i]
				if b.Kind != w.Kind || b.Direction != w.Dir || !near(b.Level, w.Level) || b.Index != w.I {
					t.Fatalf("case %d break[%d]: go %+v python %+v", ci, i, b, w)
				}
			}
			if len(levels) != len(c.Levels) {
				t.Fatalf("case %d levels: %d vs %d", ci, len(levels), len(c.Levels))
			}
			for i, l := range levels {
				w := c.Levels[i]
				if !near(l.Price, w.P) || l.Kind != w.K || l.Touches != w.T || !near(l.Band, w.B) || !near(l.Strength, w.S) {
					t.Fatalf("case %d level[%d]: go %+v python %+v", ci, i, l, w)
				}
			}
			if len(legs) != len(c.Legs) {
				t.Fatalf("case %d legs: %d vs %d", ci, len(legs), len(c.Legs))
			}
			for i, g := range legs {
				w := c.Legs[i]
				if g.Start != w.S || g.End != w.E || g.Direction != w.D || !near(g.Size, w.Z) {
					t.Fatalf("case %d leg[%d]: go %+v python %+v", ci, i, g, w)
				}
			}
			compareZones(t, "sd", ci, sd, c.SD)
			compareZones(t, "ob", ci, ob, c.OB)
			compareZones(t, "flip", ci, flip, c.Flip)
			compareZones(t, "fvg", ci, fvg, c.FVG)
			merged := techniquezone.MergeZones(concat(sd, ob, flip, fvg), p.ZoneMergeOverlap, techniquezone.ATRScalar(atr, 1)*math.Max(0, p.MaxMergedZoneATR))
			compareZones(t, "merged", ci, techniquezone.MarkMitigation(merged, bars, maxInt(0, len(bars)-1)), c.Merged)
			compareZones(t, "single", ci, techniquezone.MarkMitigation(techniquezone.AsSingleZones(concat(sd, ob, fvg)), bars, maxInt(0, len(bars)-1)), c.Single)
		}

		// Technique instances: what a detector may publish.
		tz := techniquezone.MarkMitigation(techniquezone.AsSingleZones(concat(sd, ob, fvg)), bars, maxInt(0, len(bars)-1))
		obV, sdV, fvgV := techniquezone.SourceViews(tz)
		insts := techniquezone.CollectInstances(sdV, obV, fvgV, bars, bars[len(bars)-1].Close, techniquezone.ATRScalar(atr, 1), techniquezone.TechniqueSettings{
			PipSize: math.Max(p.TG.PipSize, 1e-12), EpsilonATRFrac: 0.05, MaxZoneATR: 3.0, FVGMaxATR: p.TG.FVGMaxATR, FVGMinPips: 1.0,
			FVGEntryMaxWidthPrice: p.TG.FVGEntryMaxWidthPrice, MomentumBodyFrac: p.TG.MomentumBodyFrac,
			RetestMaxTouches: p.TG.RetestMaxTouches, InvalidationToleranceATR: p.TG.InvalidationToleranceATR,
			SweepReclaimBars: p.TG.SweepReclaimBars, MaxBreakEpisodes: p.TG.MaxBreakEpisodes,
		})
		if len(insts) != len(c.Instances) {
			t.Fatalf("case %d instances: go %d, python %d\n go %+v\n python %+v", ci, len(insts), len(c.Instances), insts, c.Instances)
		}
		for i, in := range insts {
			w := c.Instances[i]
			if in.Technique != w.Tech || in.Side != w.Side || !near(in.Low, w.Low) || !near(in.High, w.High) || in.OriginIndex != w.Origin ||
				in.Touches != w.Touches || in.Mitigated != w.Mit || !near(in.BodyFrac, w.Body) || in.EntryClipped != w.Clip {
				t.Fatalf("case %d instance[%d]:\n  go     %+v\n  python %+v", ci, i, in, w)
			}
		}
		instanceTotal += len(insts)
	}
	if full < 20 || instanceTotal < 15 {
		t.Fatalf("golden no longer exercises the chain: %d full windows, %d instances", full, instanceTotal)
	}
	t.Log(fmt.Sprintf("%d windows (%d full): zone chain identical to Python; %d technique instances identical", len(golden.Cases), full, instanceTotal))
}

func concat(parts ...[]techniquezone.Zone) []techniquezone.Zone {
	var out []techniquezone.Zone
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
