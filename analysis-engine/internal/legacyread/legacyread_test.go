package legacyread

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/momentum"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/regime"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/trendline"
)

// testConfig is the frozen production configuration at XAU scale.
func testConfig() Config {
	return Config{
		ATRLength: 14, FractalN: 2, ZigzagATRMult: 1, WindowBars: map[market.Timeframe]int{market.M5: 150, market.M15: 250, market.H1: 400},
		HTFOrder: []market.Timeframe{market.H1, market.M15}, MinPrimaryHTFWarmupBars: 50, AllowCounterTrend: true,
		LevelClusterATR: .5, LevelMinimumTouches: 2, MaximumClusterSpanMultiple: 2, DisplacementATRMult: 1.5, MomentumBodyFraction: .6,
		ZoneWidth: "body", ZoneMergeOverlap: .5, MaximumMergedZoneATR: 3, FlipAcceptBars: 2, FlipMaximumBreakAgeBars: 48, FlipBandBodyFraction: .5,
		EqualToleranceATR: .15, InducementBandATR: .3, SweepBodyFraction: .5, SweepReactBars: 3, PipSize: .1, RoundStep: 5,
		Scalp: techniquezone.ScalpConfig{
			Lookback: 48, ClusterATR: .25, ClusterPipMult: 2, MinimumTouches: 2, MinimumWickFraction: .25, EntryToleranceATR: .25,
			MaximumEdgeWidthATR: .75, MinimumWidthATR: 1, MaximumWidthATR: 6, MinimumRoomATR: .75, BreakCloses: 2, MinimumInsideCloses: 3,
			InsideLookbackBars: 24, RecentBreakoutLookback: 12, RecentBreakoutBufferATR: .15, RecentBreakoutMinSpanATR: .8,
			FallbackEnabled: true, FallbackMinConfirmations: 1, FallbackMinWidthATR: .8, FallbackMaxWidthATR: 8, FallbackWickFraction: .25,
			ProvisionalEnabled: true, PostImpulseEnabled: true, PostImpulseMinDisplaceATR: 3, PostImpulseMaxContractATR: 2.2,
			PostImpulseMinInside: 4, PostImpulseLookbackBars: 36, PostImpulseRecentBars: 6,
		},
		Compat:    techniquezone.CompatConfig{PipSize: .1, DisplacementBodyFraction: .55, DisplacementATRMult: 1.5, FractalN: 2, ATRLength: 14, LevelClusterATR: .5, RoundStep: 5, MaximumClusterSpanMultiple: 2},
		Fib:       fib.Config{EpsilonATR: .15, DeepDiscount: .382, DeepPremium: .618, EqHalfBand: .05},
		Regime:    regime.Config{ChopFilterEnabled: true, ChopLookback: 24, ChopRangeATR: 4, CoilContract: .8},
		Momentum:  momentum.Config{Lookback: 8, BullThreshold: .15, BearThreshold: -.15},
		Session:   session.Config{AsiaStartHour: 22, LondonStartHour: 7, NYStartHour: 13, DailyRolloverUTCHour: 21},
		Trendline: trendline.Config{MinimumSlopeATR: .02, MaximumSlopeATR: .15, MinimumTouchSpacingBars: 3, MinimumSpanBars: 20, MinimumValidationTouchSpacingBars: 5, ValidationTouchToleranceATR: .3, InvalidationPenetrationATR: .5, CloseViolationATR: .15, ApproachMinDistanceATR: .1, MinimumValidationFavorableExcursionATR: .1, ValidationReactionBars: 2, MinimumValidationTouches: 1, ExhaustionValidationTouches: 4, MaximumWickViolations: 2, DedupValueATR: .15, DedupSlopePercent: .1, InteractionBandATR: .2},
	}
}

func frame(tf market.Timeframe, structure string, state momentum.State, bars int) *analysiscontext.LegacyFrame {
	return &analysiscontext.LegacyFrame{Bars: make([]market.Candle, bars), Structure: structure, Momentum: state}
}

func TestHTFBiasPortsTheFrozenRules(t *testing.T) {
	cfg := testConfig()
	cases := []struct {
		name   string
		frames map[market.Timeframe]*analysiscontext.LegacyFrame
		want   string
	}{
		{"primary higher timeframe missing fails closed", map[market.Timeframe]*analysiscontext.LegacyFrame{market.M5: frame(market.M5, "up", momentum.Bull, 150)}, "unknown"},
		{"primary higher timeframe not warm fails closed", map[market.Timeframe]*analysiscontext.LegacyFrame{market.H1: frame(market.H1, "up", momentum.Neutral, 49)}, "unknown"},
		{"a decided H1 structure wins", map[market.Timeframe]*analysiscontext.LegacyFrame{market.H1: frame(market.H1, "up", momentum.Neutral, 60), market.M15: frame(market.M15, "down", momentum.Bear, 60)}, "up"},
		{"opposing momentum skips H1 for M15", map[market.Timeframe]*analysiscontext.LegacyFrame{market.H1: frame(market.H1, "up", momentum.Bear, 60), market.M15: frame(market.M15, "down", momentum.Neutral, 60)}, "down"},
		{"momentum decides a ranging structure", map[market.Timeframe]*analysiscontext.LegacyFrame{market.H1: frame(market.H1, "range", momentum.Bear, 60)}, "down"},
		{"ranging H1 hands over to M15", map[market.Timeframe]*analysiscontext.LegacyFrame{market.H1: frame(market.H1, "range", momentum.Neutral, 60), market.M15: frame(market.M15, "up", momentum.Neutral, 60)}, "up"},
		{"all configured ranging falls back to the highest other frame", map[market.Timeframe]*analysiscontext.LegacyFrame{
			market.H1: frame(market.H1, "range", momentum.Neutral, 60), market.M15: frame(market.M15, "range", momentum.Neutral, 60), market.M5: frame(market.M5, "down", momentum.Neutral, 150)}, "down"},
		{"nothing decided is range", map[market.Timeframe]*analysiscontext.LegacyFrame{market.H1: frame(market.H1, "range", momentum.Neutral, 60)}, "range"},
	}
	for _, tc := range cases {
		if got := htfBias(tc.frames, cfg); got != tc.want {
			t.Fatalf("%s: htfBias = %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestReadDirectionFollowsTheCounterTrendSetting(t *testing.T) {
	cfg := testConfig()
	frames := map[market.Timeframe]*analysiscontext.LegacyFrame{
		market.H1: frame(market.H1, "up", momentum.Neutral, 60), market.M5: frame(market.M5, "down", momentum.Neutral, 150),
	}
	read := Read(frames, market.M5, cfg)
	if read.LocalStructure != "down" || read.HTFBias != "up" || read.Direction() != market.Sell {
		t.Fatalf("counter-trend allowed: the local structure decides: %+v", read)
	}
	cfg.AllowCounterTrend = false
	if got := Read(frames, market.M5, cfg).Direction(); got != market.Buy {
		t.Fatalf("counter-trend off: the higher timeframe decides, got %q", got)
	}
	if !Read(frames, market.M5, cfg).AlignedWithHTF(market.Buy) || Read(frames, market.M5, cfg).AlignedWithHTF(market.Sell) {
		t.Fatal("htf alignment is the higher-timeframe bias against the direction")
	}
}

type capture struct {
	Timeframes map[string][][]float64 `json:"timeframes"`
}

func loadCapture(t *testing.T, name string) map[market.Timeframe][]market.Candle {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	var c capture
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatal(err)
	}
	out := map[market.Timeframe][]market.Candle{}
	for _, tf := range []market.Timeframe{market.M5, market.M15, market.H1} {
		for _, row := range c.Timeframes[string(tf)] {
			out[tf] = append(out[tf], market.Candle{Time: int64(row[0]), Open: row[1], High: row[2], Low: row[3], Close: row[4], Volume: row[5]})
		}
	}
	return out
}

// upTo returns the bars of tf that are closed by the given M5 bar's close.
func upTo(bars map[market.Timeframe][]market.Candle, closeAt int64) map[market.Timeframe][]market.Candle {
	out := map[market.Timeframe][]market.Candle{}
	for tf, series := range bars {
		minutes, _ := tf.Minutes()
		n := sort.Search(len(series), func(i int) bool { return series[i].Time+int64(minutes)*60 > closeAt })
		out[tf] = series[:n]
	}
	return out
}

func frames(t *testing.T, bars map[market.Timeframe][]market.Candle, cfg Config) map[market.Timeframe]*analysiscontext.LegacyFrame {
	t.Helper()
	var highest market.Timeframe
	for tf := range cfg.WindowBars {
		if m, _ := tf.Minutes(); highest == "" {
			highest = tf
		} else if hm, _ := highest.Minutes(); m > hm {
			highest = tf
		}
	}
	weekly := Weekly(bars[highest], highest, cfg)
	staged := map[market.Timeframe]Staged{}
	for tf := range cfg.WindowBars {
		staged[tf] = Stage(bars[tf], tf, weekly, cfg)
	}
	return Complete(staged)
}

func TestFramesAreBoundedDeterministicAndConsistent(t *testing.T) {
	cfg := testConfig()
	all := loadCapture(t, "replay-xau-production-capture-20260921.json")
	at := all[market.M5][1200].Time + 300
	input := upTo(all, at)
	a, b := frames(t, input, cfg), frames(t, input, cfg)
	for tf, window := range cfg.WindowBars {
		f := a[tf]
		want := window
		if len(input[tf]) < want {
			want = len(input[tf])
		}
		if f == nil || len(f.Bars) != want {
			t.Fatalf("%s window = %d, want %d", tf, len(f.Bars), want)
		}
		if f.Bars[len(f.Bars)-1].Time != input[tf][len(input[tf])-1].Time {
			t.Fatalf("%s frame must end on the latest closed bar", tf)
		}
		if len(f.ATR) != want || f.DetectorATR <= 0 {
			t.Fatalf("%s volatility not computed: atr=%d detector=%v", tf, len(f.ATR), f.DetectorATR)
		}
		for i := 1; i < len(f.Zones); i++ {
			if f.Zones[i-1].Score < f.Zones[i].Score {
				t.Fatalf("%s zones must be ordered by score", tf)
			}
		}
		if f.Structure != "up" && f.Structure != "down" && f.Structure != "range" {
			t.Fatalf("structure = %q", f.Structure)
		}
		if tf == market.H1 {
			for _, z := range f.Zones {
				for _, reason := range z.ScoreReasons {
					if reason == "HTF zone" {
						t.Fatal("the highest timeframe has no higher zones to score against")
					}
				}
			}
		}
		// Determinism: a recomputation gives the same facts.
		if len(a[tf].Zones) != len(b[tf].Zones) || a[tf].Structure != b[tf].Structure || a[tf].DetectorATR != b[tf].DetectorATR {
			t.Fatalf("%s frame is not deterministic", tf)
		}
	}
	sawHTF := false
	for _, tf := range []market.Timeframe{market.M15, market.M5} {
		for _, z := range a[tf].Zones {
			for _, reason := range z.ScoreReasons {
				sawHTF = sawHTF || reason == "HTF zone"
			}
		}
	}
	if !sawHTF {
		t.Fatal("on real data some lower-timeframe zone sits inside a higher-timeframe zone")
	}
	read := Read(a, market.M5, cfg)
	if read.LocalStructure != a[market.M5].Structure || !strings.Contains("up down range unknown", read.HTFBias) {
		t.Fatalf("read = %+v", read)
	}
}

func TestWindowIsTheTrailingBarsOnly(t *testing.T) {
	cfg := testConfig()
	all := loadCapture(t, "replay-gbpusd-production-capture-20261005.json")
	short := Stage(all[market.M5][:200], market.M5, nil, cfg)
	long := Stage(all[market.M5], market.M5, nil, cfg)
	if len(short.Bars()) != 150 || len(long.Bars()) != 150 {
		t.Fatalf("windows = %d/%d", len(short.Bars()), len(long.Bars()))
	}
	// The same trailing window gives the same read regardless of older history.
	prefix := Stage(all[market.M5][:len(all[market.M5])], market.M5, nil, cfg)
	again := Stage(all[market.M5][len(all[market.M5])-150:], market.M5, nil, cfg)
	if prefix.stage.frame.Structure != again.stage.frame.Structure || prefix.stage.frame.DetectorATR != again.stage.frame.DetectorATR {
		t.Fatal("older history must not influence the detector-contract read")
	}
	empty := Stage(nil, market.M5, nil, cfg)
	if len(empty.Bars()) != 0 || empty.stage.frame.Structure != "range" {
		t.Fatal("an empty history is an undecided frame")
	}
}
