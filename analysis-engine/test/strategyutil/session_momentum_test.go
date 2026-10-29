package strategyutil_test

import (
	"path/filepath"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// day returns three weekdays of M5 and M15 candles starting at 00:00 UTC of start, drifting up by `perBar` per M5 candle between London open and New
// York open (gold's London drift) and flat elsewhere, so the London move is known.
func series(start time.Time, hours int, londonOpen, nyOpen int64, perBar float64) (m5, m15 []market.Candle) {
	price := 4000.0
	for t := start.Unix(); t < start.Unix()+int64(hours)*3600; t += 300 {
		open := price
		if t >= londonOpen && t < nyOpen {
			price += perBar
		}
		m5 = append(m5, market.Candle{Time: t, Open: open, High: price + 1, Low: open - 1, Close: price})
	}
	for i := 0; i+2 < len(m5); i += 3 {
		if m5[i].Time%900 != 0 {
			continue
		}
		a, b, c := m5[i], m5[i+1], m5[i+2]
		hi, lo := a.High, a.Low
		for _, x := range []market.Candle{b, c} {
			if x.High > hi {
				hi = x.High
			}
			if x.Low < lo {
				lo = x.Low
			}
		}
		m15 = append(m15, market.Candle{Time: a.Time, Open: a.Open, High: hi, Low: lo, Close: c.Close})
	}
	return m5, m15
}

func contextAt(t *testing.T, m5, m15 []market.Candle) *analysiscontext.MarketContext {
	t.Helper()
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := engine.SessionConfigFromConfig(doc)
	if err != nil {
		t.Fatal(err)
	}
	return &analysiscontext.MarketContext{
		Symbol:  "XAU",
		Session: analysiscontext.SessionContext{Timeframe: market.M5, State: session.Update(m5, cfg)},
		Timeframes: map[market.Timeframe]*analysiscontext.TimeframeContext{
			market.M5: {Timeframe: market.M5, Candles: m5}, market.M15: {Timeframe: market.M15, Candles: m15},
		},
	}
}

// londonOpenOn is the instant the session calendar makes the Asia range of the given day knowable (London open), taken from a state built over
// that day's candles.
func londonOpenOn(t *testing.T, day time.Time) time.Time {
	t.Helper()
	m5, m15 := series(day.Add(-24*time.Hour), 24+14, 0, 0, 0)
	var at int64
	for _, l := range contextAt(t, m5, m15).Session.State.Levels {
		if l.Name == "ASIA_H" && l.Final && l.AvailableAt > at {
			at = l.AvailableAt
		}
	}
	return time.Unix(at, 0).UTC()
}

// The clocks went back in the UK on Sunday 25 Oct 2026: London opens an hour later in UTC after it, and the boundaries the momentum is read from
// follow the calendar, not a fixed UTC hour.
func TestSessionBoundariesFollowTheLondonClockChange(t *testing.T) {
	before := londonOpenOn(t, time.Date(2026, 10, 23, 0, 0, 0, 0, time.UTC))
	after := londonOpenOn(t, time.Date(2026, 10, 26, 0, 0, 0, 0, time.UTC))
	if before.Hour() != 7 || after.Hour() != 8 {
		t.Fatalf("London opens at %02d:00 UTC on 23 Oct and %02d:00 UTC on 26 Oct, want 07:00 and 08:00", before.Hour(), after.Hour())
	}
}

func TestSessionMomentumReadsTheLondonMoveCausally(t *testing.T) {
	// 26 Oct 2026 is the Monday after the clocks went back in the UK: London opens at 08:00 UTC, New York at 14:00 UTC.
	day := time.Date(2026, 10, 26, 0, 0, 0, 0, time.UTC)
	london, ny := day.Add(8*time.Hour).Unix(), day.Add(13*time.Hour+30*time.Minute).Unix()
	m5, m15 := series(day.Add(-24*time.Hour), 24+20, london, ny, 0.08)
	ctx := contextAt(t, m5, m15)
	if ctx.Session.State.Active != session.NY {
		t.Skipf("the session calendar puts %v in %q, not New York", time.Unix(m5[len(m5)-1].Time, 0).UTC(), ctx.Session.State.Active)
	}
	at := m5[len(m5)-1].Time
	dir, net, ok := strategyutil.SessionMomentum(ctx, at)
	if !ok || dir != market.Buy || net <= 0 {
		t.Fatalf("a rising London must read as a BUY move: %v %v %v", dir, net, ok)
	}
	if code, component, has := strategyutil.SessionMomentumEvidence(ctx, at, market.Buy, 0.01); !has || code != "session_momentum_aligned" || component <= 0 {
		t.Fatalf("aligned: %q %v %v", code, component, has)
	}
	if code, component, has := strategyutil.SessionMomentumEvidence(ctx, at, market.Sell, 0.01); !has || code != "session_momentum_opposed" || component >= 0 {
		t.Fatalf("opposed: %q %v %v", code, component, has)
	}
	if code, _, has := strategyutil.SessionMomentumEvidence(ctx, at, market.Buy, 1e6); !has || code != "" {
		t.Fatalf("a move below the minimum earns no evidence code: %q", code)
	}
}

func TestSessionMomentumNeverReadsACandleAfterTheConfirmation(t *testing.T) {
	day := time.Date(2026, 10, 26, 0, 0, 0, 0, time.UTC)
	london, ny := day.Add(8*time.Hour).Unix(), day.Add(13*time.Hour+30*time.Minute).Unix()
	m5, m15 := series(day.Add(-24*time.Hour), 24+20, london, ny, 0.08)
	ctx := contextAt(t, m5, m15)
	if ctx.Session.State.Active != session.NY {
		t.Skip("not New York")
	}
	at := m5[len(m5)-1].Time
	_, want, ok := strategyutil.SessionMomentum(ctx, at)
	if !ok {
		t.Fatal("no reading")
	}
	// Candles after the confirmation, however wild, are not read.
	last := m5[len(m5)-1]
	ctx.Timeframes[market.M5].Candles = append(ctx.Timeframes[market.M5].Candles,
		market.Candle{Time: last.Time + 300, Open: last.Close, High: last.Close + 500, Low: last.Close - 500, Close: last.Close - 400})
	_, got, ok := strategyutil.SessionMomentum(ctx, at)
	if !ok || got != want {
		t.Fatalf("a candle after the confirmation changed the reading: %v -> %v", want, got)
	}
}

func TestSessionMomentumIsAbsentWithoutBoundariesOrOutsideNewYork(t *testing.T) {
	day := time.Date(2026, 10, 26, 0, 0, 0, 0, time.UTC)
	london, ny := day.Add(8*time.Hour).Unix(), day.Add(13*time.Hour+30*time.Minute).Unix()
	m5, m15 := series(day.Add(-24*time.Hour), 24+20, london, ny, 0.08)
	ctx := contextAt(t, m5, m15)
	at := m5[len(m5)-1].Time
	// A confirmation before New York opened has no London move to read.
	if _, _, ok := strategyutil.SessionMomentum(ctx, day.Add(10*time.Hour).Unix()); ok {
		t.Fatal("a reading before the New York open")
	}
	ctx.Session.State.Active = session.London
	if _, _, ok := strategyutil.SessionMomentum(ctx, at); ok {
		t.Fatal("a reading outside New York")
	}
	ctx.Session.State.Active = session.NY
	ctx.Session.State.Levels = nil
	if _, _, ok := strategyutil.SessionMomentum(ctx, at); ok {
		t.Fatal("a reading without session boundaries")
	}
	if _, _, ok := strategyutil.SessionMomentum(nil, at); ok {
		t.Fatal("a reading without a context")
	}
}
