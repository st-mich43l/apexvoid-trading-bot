// Package keylevelgolden_test prints (and guards) the reference the TradingView key-level layer is ported against: keylevel.Cluster and keylevel.Role on the
// raw fractal pivots (fractal_n = 2) of the last `window` closed M5 bars of the committed XAU capture, with the canonical last-bar ATR.
//
// It also records what the live key_level STRATEGY reads for the same bars (legacyread's techniquezone.KeyLevels on zigzag-filtered swings and the median
// ATR), so the difference between the two definitions is measured, not assumed.
//
//	KEYLEVEL_GOLDEN_OUT=../tradingview/reference/fixtures/keylevel-golden-scalpwindow-m5.json go test ./test/keylevelgolden -run TestKeyLevelGolden
//
// Without the variable the test fails when the committed golden no longer matches.
package keylevelgolden_test

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

const (
	captureName = "scalpwindow-xau-20261103.json"
	window      = 150
	fractalN    = 2
)

type swingRow struct {
	Bar   int     `json:"bar"`
	Time  int64   `json:"time"`
	Kind  string  `json:"kind"`
	Price float64 `json:"price"`
}

type levelRow struct {
	Price   float64 `json:"price"`
	Kind    string  `json:"kind"`
	Touches int     `json:"touches"`
	Band    float64 `json:"band"`
	Role    string  `json:"role,omitempty"`
}

type golden struct {
	Capture        string     `json:"capture"`
	Timeframe      string     `json:"timeframe"`
	Window         int        `json:"window"`
	FractalN       int        `json:"fractal_n"`
	LastBarTime    int64      `json:"last_bar_time"`
	ATR            float64    `json:"atr"`
	MedianATR      float64    `json:"median_atr_of_window"`
	ClusterATR     float64    `json:"cluster_atr"`
	RoundStep      float64    `json:"round_step"`
	MinimumTouches int        `json:"minimum_touches"`
	SpanMultiple   float64    `json:"maximum_cluster_span_multiple"`
	BreakoutBars   int        `json:"breakout_accept_bars"`
	LastClose      float64    `json:"last_close"`
	Swings         []swingRow `json:"swings"`
	Levels         []levelRow `json:"levels"`
	// StrategyLevels is what the live key_level strategy reads for the same window (legacyread: zigzag-filtered fractals, median ATR, no wick re-count).
	StrategyLevels []levelRow `json:"strategy_levels"`
	// StrategySwings are the zigzag-filtered fractals those levels are built from (techniquezone.FindSwings), with absolute bar indices.
	StrategySwings []swingRow `json:"strategy_swings"`
}

// fractals mirrors techniquezone.fractalCandidates without the zigzag filter: the left bars strictly lower, the right bars not higher.
func fractals(bars []market.Candle, n int) []swingRow {
	var out []swingRow
	for i := n; i < len(bars)-n; i++ {
		maxHigh, minLow := bars[i-n].High, bars[i-n].Low
		for j := i - n; j <= i+n; j++ {
			if bars[j].High > maxHigh {
				maxHigh = bars[j].High
			}
			if bars[j].Low < minLow {
				minLow = bars[j].Low
			}
		}
		highOK, lowOK := bars[i].High == maxHigh, bars[i].Low == minLow
		for j := i - n; j < i; j++ {
			highOK = highOK && bars[j].High < bars[i].High
			lowOK = lowOK && bars[j].Low > bars[i].Low
		}
		if highOK {
			out = append(out, swingRow{Bar: i, Time: bars[i].Time, Kind: "high", Price: bars[i].High})
		}
		if lowOK {
			out = append(out, swingRow{Bar: i, Time: bars[i].Time, Kind: "low", Price: bars[i].Low})
		}
	}
	return out
}

func build(t *testing.T) golden {
	capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", captureName))
	if err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	settings, err := engine.LoadSettings(doc, market.M5, false)
	if err != nil {
		t.Fatal(err)
	}
	if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
		t.Fatal(err)
	}
	var candles []market.Candle
	for _, row := range capture.Timeframes["M5"] {
		candles = append(candles, market.Candle{Time: int64(row[0]), Open: row[1], High: row[2], Low: row[3], Close: row[4], Volume: row[5]})
	}
	atrSeries, err := indicator.CanonicalATR(candles, settings.ATR.Length, settings.ATR.Algorithm)
	if err != nil {
		t.Fatal(err)
	}
	offset := len(candles) - window
	bars := candles[offset:]
	atr := atrSeries[len(atrSeries)-1]
	cfg := settings.KeyLevel
	breakout := 2 // analysis.strategies.key_level.breakout_accept_bars

	g := golden{Capture: captureName, Timeframe: "M5", Window: window, FractalN: fractalN, LastBarTime: bars[len(bars)-1].Time, ATR: atr,
		ClusterATR: cfg.ClusterATR, RoundStep: cfg.RoundStep, MinimumTouches: cfg.MinimumTouches, SpanMultiple: cfg.MaximumClusterSpanMultiple,
		BreakoutBars: breakout, LastClose: bars[len(bars)-1].Close}
	var swings []structure.Swing
	for _, s := range fractals(bars, fractalN) {
		s.Bar += offset
		g.Swings = append(g.Swings, s)
		kind := structure.SwingHigh
		if s.Kind == "low" {
			kind = structure.SwingLow
		}
		swings = append(swings, structure.Swing{ID: "s", Kind: kind, Price: market.Price(s.Price), Time: s.Time})
	}
	closes := make([]float64, len(bars))
	for i, b := range bars {
		closes[i] = b.Close
	}
	row := func(l keylevel.Level, tolerance float64) levelRow {
		band := l.Band
		if band < tolerance {
			band = tolerance
		}
		role := keylevel.Role(l.Kind.String(), market.Price(float64(l.Price)-band), market.Price(float64(l.Price)+band), closes, breakout)
		return levelRow{Price: float64(l.Price), Kind: l.Kind.String(), Touches: l.Touches, Band: l.Band, Role: role.String()}
	}
	// no candles: the wick-touch re-count needs every bar, which the Pine layer does not keep
	for _, l := range keylevel.Cluster(nil, atr, swings, cfg) {
		g.Levels = append(g.Levels, row(l, atr*cfg.ClusterATR))
	}

	lr := settings.LegacyRead
	atrWindow := techniquezone.ATRSeries(bars, lr.ATRLength)
	g.MedianATR = techniquezone.ATRScalar(atrWindow, 1)
	legacySwings := techniquezone.FindSwings(bars, lr.FractalN, lr.ZigzagPct, lr.ZigzagATRMult, atrWindow, -1)
	for _, sw := range legacySwings {
		g.StrategySwings = append(g.StrategySwings, swingRow{Bar: sw.Index + offset, Time: bars[sw.Index].Time, Kind: sw.Kind, Price: sw.Price})
	}
	for _, l := range techniquezone.KeyLevels(legacySwings, atrWindow, lr.LevelClusterATR, lr.RoundStep, lr.LevelMinimumTouches, lr.MaximumClusterSpanMultiple, nil) {
		tolerance := g.MedianATR * lr.LevelClusterATR
		band := l.Band
		if band < tolerance {
			band = tolerance
		}
		role := keylevel.Role(l.Kind, market.Price(l.Price-band), market.Price(l.Price+band), closes, breakout)
		g.StrategyLevels = append(g.StrategyLevels, levelRow{Price: l.Price, Kind: l.Kind, Touches: l.Touches, Band: l.Band, Role: role.String()})
	}
	return g
}

func TestKeyLevelGolden(t *testing.T) {
	got, err := json.MarshalIndent(build(t), "", " ")
	if err != nil {
		t.Fatal(err)
	}
	got = append(got, '\n')
	path := filepath.Join("..", "..", "..", "tradingview", "reference", "fixtures", "keylevel-golden-scalpwindow-m5.json")
	if out := os.Getenv("KEYLEVEL_GOLDEN_OUT"); out != "" {
		if err := os.WriteFile(out, got, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("no committed golden (%v): regenerate with KEYLEVEL_GOLDEN_OUT", err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("the committed key-level golden no longer matches keylevel.Cluster / Role; regenerate with KEYLEVEL_GOLDEN_OUT=%s", path)
	}
}
