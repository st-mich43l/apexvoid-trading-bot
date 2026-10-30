package legacyread

import (
	"math"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
)

// The frame's session levels must come from its whole in-memory history, not from the 150-bar (12.5 h) detection window: at 08:00 UTC that window starts at
// 19:30 the evening before, so the "previous day high/low" it reports is a fragment of yesterday.

func productionSession(t *testing.T) session.Config {
	t.Helper()
	calendar, err := session.NewCalendar("Asia/Tokyo", 7, "Europe/London", 8, "America/New_York", 9, "America/New_York", 17)
	if err != nil {
		t.Fatal(err)
	}
	return session.Config{AsiaStartHour: 22, LondonStartHour: 7, NYStartHour: 13, DailyRolloverUTCHour: 21, Calendar: calendar}
}

// m5Series is a continuous M5 series from start (inclusive) to end (exclusive) whose prices are a pure function of the bar's open time, so any extreme
// can be recomputed by brute force.
func m5Series(start, end time.Time, price func(t int64) float64) []market.Candle {
	var out []market.Candle
	for ts := start.Unix(); ts < end.Unix(); ts += 300 {
		mid := price(ts)
		out = append(out, market.Candle{Time: ts, Open: mid, High: mid + 0.2, Low: mid - 0.2, Close: mid, Volume: 1})
	}
	return out
}

// wobble is deterministic, bounded and different on every bar, so the extremes of a span are unique.
func wobble(ts int64) float64 {
	return 100 + 3*math.Sin(float64(ts)/7919.0) + 2*math.Sin(float64(ts)/1873.0) + float64(ts%977)/977.0
}

func bruteExtremes(bars []market.Candle, from, to int64) (high, low float64) {
	high, low = math.Inf(-1), math.Inf(1)
	for _, b := range bars {
		if b.Time >= from && b.Time < to {
			high, low = math.Max(high, b.High), math.Min(low, b.Low)
		}
	}
	return high, low
}

// level is the latest level of that name (a session keeps its two most recently closed occurrences).
func level(sessions []sessionRef, name string) (float64, bool) {
	var best sessionRef
	found := false
	for _, s := range sessions {
		if s.Name == name && (!found || s.AvailableAt > best.AvailableAt) {
			best, found = s, true
		}
	}
	return best.Price, found
}

type sessionRef = struct {
	Name        string
	Price       float64
	Swept       bool
	AvailableAt int64
}

func stageSessions(candles []market.Candle, cfg Config) []sessionRef {
	staged := Stage(candles, market.M5, nil, cfg)
	var out []sessionRef
	for _, s := range staged.stage.frame.Sessions {
		out = append(out, sessionRef{s.Name, s.Price, s.Swept, s.AvailableAt})
	}
	return out
}

func TestPreviousDayLevelsCoverTheWholeOfYesterdayNotTheDetectionWindow(t *testing.T) {
	cfg := testConfig()
	cfg.Session = productionSession(t)
	ny, _ := time.LoadLocation("America/New_York")
	// Thursday 22 Oct 2026 08:00 UTC; 3.3 days of M5 history (about 950 bars).
	last := time.Date(2026, 10, 22, 8, 0, 0, 0, time.UTC)
	candles := m5Series(last.Add(-950*5*time.Minute), last, wobble)

	// Yesterday's trading day is Tue 20 Oct 17:00 NY to Wed 21 Oct 17:00 NY.
	from := time.Date(2026, 10, 20, 17, 0, 0, 0, ny).Unix()
	to := time.Date(2026, 10, 21, 17, 0, 0, 0, ny).Unix()
	wantHigh, wantLow := bruteExtremes(candles, from, to)

	got := stageSessions(candles, cfg)
	pdh, okH := level(got, "PDH")
	pdl, okL := level(got, "PDL")
	if !okH || !okL {
		t.Fatalf("no PDH/PDL in %v", got)
	}
	if math.Abs(pdh-wantHigh) > 1e-9 || math.Abs(pdl-wantLow) > 1e-9 {
		t.Fatalf("PDH/PDL %.4f/%.4f, want the whole previous trading day %.4f/%.4f", pdh, pdl, wantHigh, wantLow)
	}

	// The pre-fix reading: the same call on the 150-bar window sees the evening fragment only. This is what proves the test would have caught the bug.
	frozen := cfg
	frozen.FrozenSessionWindows = true
	old := stageSessions(candles, frozen)
	oldH, _ := level(old, "PDH")
	oldL, _ := level(old, "PDL")
	fragHigh, fragLow := bruteExtremes(candles[len(candles)-150:], from, to)
	if math.Abs(oldH-fragHigh) > 1e-9 || math.Abs(oldL-fragLow) > 1e-9 {
		t.Fatalf("the frozen window should report the fragment %.4f/%.4f, got %.4f/%.4f", fragHigh, fragLow, oldH, oldL)
	}
	if math.Abs(oldH-pdh) < 1e-9 && math.Abs(oldL-pdl) < 1e-9 {
		t.Fatal("the window-derived PDH/PDL equal the full-day ones: the fixture does not distinguish the bug")
	}
}

func TestSessionLevelsAreFinalAndAvailableAtTheLastBar(t *testing.T) {
	cfg := testConfig()
	cfg.Session = productionSession(t)
	last := time.Date(2026, 10, 22, 8, 0, 0, 0, time.UTC)
	candles := m5Series(last.Add(-950*5*time.Minute), last, wobble)
	lastTime := candles[len(candles)-1].Time
	got := stageSessions(candles, cfg)
	if len(got) == 0 {
		t.Fatal("no session levels")
	}
	for _, s := range got {
		if s.AvailableAt <= 0 || s.AvailableAt > lastTime {
			t.Errorf("%s is not causal: available at %d, last bar %d", s.Name, s.AvailableAt, lastTime)
		}
	}
}

// Across the end of British summer time (Sun 25 Oct 2026, 02:00 -> 01:00 UTC) the London session opens at 07:00 UTC before it and 08:00 UTC after it; across
// the end of US daylight time (Sun 1 Nov 2026) the trading day rolls over at 21:00 UTC before it and 22:00 UTC after it.
func TestSessionLevelsFollowDaylightSavingChanges(t *testing.T) {
	cfg := testConfig()
	cfg.Session = productionSession(t)
	ny, _ := time.LoadLocation("America/New_York")
	london, _ := time.LoadLocation("Europe/London")

	// London: last bar Mon 26 Oct 16:00 UTC. The London session of Mon 26 Oct is 08:00 GMT = 08:00 UTC, the one of Fri 23 Oct was 08:00 BST = 07:00 UTC.
	last := time.Date(2026, 10, 26, 16, 0, 0, 0, time.UTC)
	candles := m5Series(last.Add(-1200*5*time.Minute), last, wobble)
	got := stageSessions(candles, cfg)
	monStart := time.Date(2026, 10, 26, 8, 0, 0, 0, london)
	if monStart.UTC().Hour() != 8 {
		t.Fatalf("fixture: London on 26 Oct opens at %v UTC", monStart.UTC())
	}
	// the session ends where New York opens (09:00 New York: 13:00 UTC on 26 Oct, still daylight time)
	monEnd := time.Date(2026, 10, 26, 9, 0, 0, 0, ny)
	h, l := bruteExtremes(candles, monStart.Unix(), monEnd.Unix())
	if gotH, ok := level(got, "LONDON_H"); !ok || math.Abs(gotH-h) > 1e-9 {
		t.Errorf("LONDON_H %v (%v), want the 08:00-13:00 UTC extreme %v", gotH, ok, h)
	}
	if gotL, ok := level(got, "LONDON_L"); !ok || math.Abs(gotL-l) > 1e-9 {
		t.Errorf("LONDON_L %v (%v), want %v", gotL, ok, l)
	}

	// Sunday 25 Oct is the first day after the change: its London session opens at 08:00 GMT = 08:00 UTC.
	sunStart := time.Date(2026, 10, 25, 8, 0, 0, 0, london)
	sunH, _ := bruteExtremes(candles, sunStart.Unix(), time.Date(2026, 10, 25, 9, 0, 0, 0, ny).Unix())
	var earlier []float64
	for _, s := range got {
		if s.Name == "LONDON_H" && s.AvailableAt < monStart.Unix() {
			earlier = append(earlier, s.Price)
		}
	}
	if len(earlier) != 1 || math.Abs(earlier[0]-sunH) > 1e-9 || sunStart.UTC().Hour() != 8 {
		t.Errorf("Sunday's LONDON_H %v, want the 08:00-13:00 UTC extreme %v", earlier, sunH)
	}

	// The day before the change (Sat 24 Oct, British summer time) London opened at 08:00 BST = 07:00 UTC.
	satLast := time.Date(2026, 10, 24, 16, 0, 0, 0, time.UTC)
	satBars := m5Series(satLast.Add(-1200*5*time.Minute), satLast, wobble)
	satStart := time.Date(2026, 10, 24, 8, 0, 0, 0, london)
	if satStart.UTC().Hour() != 7 {
		t.Fatalf("fixture: London on 24 Oct opens at %v UTC", satStart.UTC())
	}
	satH, _ := bruteExtremes(satBars, satStart.Unix(), time.Date(2026, 10, 24, 9, 0, 0, 0, ny).Unix())
	if gotSat, ok := level(stageSessions(satBars, cfg), "LONDON_H"); !ok || math.Abs(gotSat-satH) > 1e-9 {
		t.Errorf("Saturday's LONDON_H %v (%v), want the 07:00-13:00 UTC extreme %v", gotSat, ok, satH)
	}

	// New York: last bar Tue 3 Nov 08:00 UTC; the previous trading day is Mon 2 Nov 17:00 EST (22:00 UTC) back to Fri... the day before 17:00 EDT/EST.
	end := time.Date(2026, 11, 3, 8, 0, 0, 0, time.UTC)
	nov := m5Series(end.Add(-1000*5*time.Minute), end, wobble)
	from := time.Date(2026, 11, 1, 17, 0, 0, 0, ny).Unix() // Sun 1 Nov 17:00 EST = 22:00 UTC
	to := time.Date(2026, 11, 2, 17, 0, 0, 0, ny).Unix()   // Mon 2 Nov 17:00 EST = 22:00 UTC
	if time.Unix(from, 0).UTC().Hour() != 22 {
		t.Fatalf("fixture: rollover on 1 Nov is %v UTC", time.Unix(from, 0).UTC())
	}
	wantH, wantL := bruteExtremes(nov, from, to)
	gotNov := stageSessions(nov, cfg)
	pdh, _ := level(gotNov, "PDH")
	pdl, _ := level(gotNov, "PDL")
	if math.Abs(pdh-wantH) > 1e-9 || math.Abs(pdl-wantL) > 1e-9 {
		t.Errorf("PDH/PDL %.4f/%.4f, want %.4f/%.4f for the trading day ending Mon 2 Nov 17:00 EST", pdh, pdl, wantH, wantL)
	}
}
