package scalpstrategies_test

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/asiarange"
)

// The 2026 clock changes the committed captures all predate and the live period follows: London left daylight saving on Sunday 25 October (the London open
// moves from 07:00 to 08:00 UTC) and New York on Sunday 1 November (the New York open moves from 13:00 to 14:00 UTC).
var dstDays = []struct {
	name                     string
	year                     int
	month                    time.Month
	day                      int
	londonOpenUTC, nyOpenUTC int
}{
	{"Fri 23 Oct (London BST, New York EDT)", 2026, time.October, 23, 7, 13},
	{"Mon 26 Oct (London GMT, New York EDT)", 2026, time.October, 26, 8, 13},
	{"Fri 30 Oct (London GMT, New York EDT)", 2026, time.October, 30, 8, 13},
	{"Mon 2 Nov (London GMT, New York EST)", 2026, time.November, 2, 8, 14},
}

// raidDay builds one synthetic trading morning: a quiet Asia session whose extremes are exactly 4130 and 4100 (spikes well inside the window), then, from the
// London open, the canonical accepted bullish raid of the Asia low (a sweep 14 pips under it that closes back above, then a displacement close above the
// lower swing high), then a quiet London session through the New York open. It returns every M5 candle, the index of the structure-shift candle that
// confirms the raid, and the M15 candles known at that moment.
func raidDay(londonOpen, asiaOpen, nyOpen int64) (m5 []market.Candle, shift int, m15 []market.Candle) {
	at := func(i int64) int64 { return londonOpen + i*300 }
	last := 4104.0
	bar := func(t int64, closePrice, upper, lower float64) {
		open := last
		m5 = append(m5, market.Candle{Time: t, Open: open, Close: closePrice, High: maxf(open, closePrice) + upper, Low: minf(open, closePrice) - lower})
		last = closePrice
	}
	// Asia and the hours before it: a range-2 filler (the Wilder ATR settles at 2.0), with the two spikes that make the Asia extremes 4130 / 4100.
	start := asiaOpen - 6*3600
	highSpike, lowSpike := asiaOpen+3600, asiaOpen+3900
	for t, i := start, 0; t < londonOpen; t, i = t+300, i+1 {
		switch t {
		case highSpike:
			m5 = append(m5, market.Candle{Time: t, Open: 4104, High: 4130, Low: 4103.5, Close: 4104.2})
			last = 4104.2
		case lowSpike:
			m5 = append(m5, market.Candle{Time: t, Open: 4104.2, High: 4104.7, Low: 4100, Close: 4104})
			last = 4104
		default:
			if i%2 == 0 {
				bar(t, 4104.3, 0.7, 0.7)
			} else {
				bar(t, 4103.7, 0.7, 0.7)
			}
		}
	}
	last = 4104.0
	// From London open: the canonical bullish raid (the same candles the unit tests use, relative to the London open).
	for i, step := range []struct{ c, u, l float64 }{
		{4105.2, 0.4, 0.3}, {4106.0, 0.4, 0.3}, {4105.0, 0.1, 0.4}, {4103.6, 0.1, 0.4}, {4101.0, 0.3, 0.4},
		{4101.4, 0.2, 2.4}, // sweep: low 4098.6, 14 pips under the Asia low, closes back above
		{4102.0, 0.3, 0.2},
		{4107.2, 0.2, 0.1}, // closes above the swing high 4106.4: structure shift
	} {
		bar(at(int64(i)), step.c, step.u, step.l)
	}
	shift = len(m5) - 1
	// Then a quiet London morning through the New York open and a little beyond, so the London window closes.
	for t, i := at(8), 0; t < nyOpen+3600; t, i = t+300, i+1 {
		if i%2 == 0 {
			bar(t, 4107.5, 0.5, 0.5)
		} else {
			bar(t, 4106.9, 0.5, 0.5)
		}
	}
	// M15 candles of range 8 (M15 ATR 8) closed by the London open: the Asia range is 30 / 8 = 3.75 ATR, inside the strategy's window.
	for i := 0; i < 40; i++ {
		open := 4114.5
		if i%2 == 1 {
			open = 4115.5
		}
		m15 = append(m15, market.Candle{Time: londonOpen - int64(40-i)*900, Open: open, High: 4119, Low: 4111, Close: 4115 + (4115 - open)})
	}
	return m5, shift, m15
}

func maxf(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func minf(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

// TestAsiaRangeIsAnchoredAndPublishesOnEachSideOfTheClockChanges is the DST suspect of the 0-live-opportunities finding: every committed capture predates the 25 Oct London
// change and the 1 Nov New York change. On a day on each side of both it checks that the engine's real session calendar finalizes ASIA_H / ASIA_L at the London open
// of THAT date (07:00 UTC in BST, 08:00 in GMT), that the strategy's active window starts there, that the London levels finalize at that date's New York open, and
// that a London raid in the window publishes.
func TestAsiaRangeIsAnchoredAndPublishesOnEachSideOfTheClockChanges(t *testing.T) {
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	sessionCfg, err := engine.SessionConfigFromConfig(doc)
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
	var asia *asiarange.Strategy
	for _, cfg := range settings.Strategies {
		if cfg.ID == "asia_range" {
			s, err := asiarange.New(cfg)
			if err != nil {
				t.Fatal(err)
			}
			asia, _ = s.(*asiarange.Strategy)
		}
	}
	if asia == nil {
		t.Fatal("asia_range must construct from the canonical configuration")
	}
	for _, day := range dstDays {
		t.Run(day.name, func(t *testing.T) {
			utc := func(h int) int64 { return time.Date(day.year, day.month, day.day, h, 0, 0, 0, time.UTC).Unix() }
			londonOpen, nyOpen := utc(day.londonOpenUTC), utc(day.nyOpenUTC)
			// Tokyo has no daylight saving: Asia opens at 22:00 UTC the evening before.
			asiaOpen := time.Date(day.year, day.month, day.day, 22, 0, 0, 0, time.UTC).AddDate(0, 0, -1).Unix()
			m5, shift, m15 := raidDay(londonOpen, asiaOpen, nyOpen)

			// 1. The Asia range is finalized at THIS date's London open, and London at its New York open.
			full := session.Update(m5, sessionCfg)
			wantAvailable := map[string]int64{"ASIA_H": londonOpen, "ASIA_L": londonOpen, "LONDON_H": nyOpen, "LONDON_L": nyOpen}
			found := map[string]bool{}
			for _, level := range full.Levels {
				want, ok := wantAvailable[level.Name]
				if !ok || !level.Final || level.AvailableAt != want {
					continue
				}
				found[level.Name] = true
				if level.Name == "ASIA_H" && float64(level.Price) != 4130 || level.Name == "ASIA_L" && float64(level.Price) != 4100 {
					t.Errorf("%s = %v, want the Asia extreme", level.Name, level.Price)
				}
			}
			for name := range wantAvailable {
				if !found[name] {
					t.Errorf("no finalized %s with AvailableAt %s; levels: %+v", name, time.Unix(wantAvailable[name], 0).UTC().Format(time.RFC3339), full.Levels)
				}
			}

			// 2. The strategy reads exactly the information that existed when the raid was confirmed.
			upTo := m5[:shift+1]
			ctx := &analysiscontext.MarketContext{
				Symbol: "XAU",
				Legacy: &analysiscontext.LegacyRead{LocalStructure: "up", HTFBias: "up", AllowCounterTrend: true},
				Session: analysiscontext.SessionContext{
					Timeframe: market.M5,
					State:     session.Update(upTo, sessionCfg),
				},
				Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
					market.M5:  {Timeframe: market.M5, Candles: upTo},
					market.M15: {Timeframe: market.M15, Candles: m15},
				},
			}
			analysis, candidates := asia.Analyze(ctx)
			if !analysis.Anchored {
				t.Fatalf("the strategy found no anchor on %s: %+v", day.name, analysis)
			}
			if len(candidates) != 1 {
				t.Fatalf("a London raid inside the window must publish on %s: %d candidates, rejections %+v", day.name, len(candidates), analysis.Rejections)
			}
			if c := candidates[0]; c.Direction != market.Buy || c.CreatedAt != m5[shift].Time {
				t.Fatalf("candidate %+v", c)
			}
			// The window starts at the London open of the date, not at a fixed UTC hour: the first candle that may start an episode is the London open candle.
			if analysis.Setups[0].Anchor.AvailableAt != londonOpen {
				t.Errorf("anchor AvailableAt %d, want the London open %d", analysis.Setups[0].Anchor.AvailableAt, londonOpen)
			}
		})
	}
}
