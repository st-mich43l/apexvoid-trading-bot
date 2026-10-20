package session_test

import (
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/session"
)

func marketCalendar(t *testing.T) session.Config {
	t.Helper()
	calendar, err := session.NewCalendar("Asia/Tokyo", 7, "Europe/London", 8, "America/New_York", 9, "America/New_York", 17)
	if err != nil {
		t.Fatal(err)
	}
	return session.Config{AsiaStartHour: 22, LondonStartHour: 7, NYStartHour: 13, DailyRolloverUTCHour: 21, Calendar: calendar}
}

// bars returns one candle per hour from start for n hours; the high of each is its hour index so the
// extreme of any window is the last candle in it.
func bars(start time.Time, n int) []market.Candle {
	out := make([]market.Candle, n)
	for i := range out {
		high := float64(100 + i)
		out[i] = market.Candle{Time: start.Add(time.Duration(i) * time.Hour).Unix(), Open: high - 1, High: high, Low: high - 2, Close: high - 1}
	}
	return out
}

func utc(year int, month time.Month, day, hour int) time.Time {
	return time.Date(year, month, day, hour, 0, 0, 0, time.UTC)
}

// The London session opens at 08:00 London: 07:00 UTC while Britain is on summer
// time and 08:00 UTC after the clocks go back (25 Oct 2026). A fixed 07:00 UTC window would
// start the winter session an hour early and put its first hour in the Asia level.
func TestLondonOpensOnTheLondonClockAcrossTheDaylightSavingChange(t *testing.T) {
	cfg := marketCalendar(t)
	for _, tc := range []struct {
		name  string
		day   time.Time // the day under test, 00:00 UTC
		first int       // UTC hour London opens
	}{
		{"summer time", utc(2026, time.October, 20, 0), 7},
		{"winter time", utc(2026, time.October, 27, 0), 8},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Candles from the previous evening through the test day's noon.
			candles := bars(tc.day.Add(-26*time.Hour), 40)
			at := func(hour int) session.State {
				var upto []market.Candle
				for _, c := range candles {
					if c.Time <= tc.day.Add(time.Duration(hour)*time.Hour).Unix() {
						upto = append(upto, c)
					}
				}
				return session.Update(upto, cfg)
			}
			if got := at(tc.first - 1).Active; got != session.Asia {
				t.Fatalf("the hour before London opens (%02d:00 UTC) is %q, want ASIA", tc.first-1, got)
			}
			if got := at(tc.first).Active; got != session.London {
				t.Fatalf("London's opening hour (%02d:00 UTC) is %q, want LONDON", tc.first, got)
			}
		})
	}
}

func TestTheTradingDayRollsOverAtSeventeenHundredNewYork(t *testing.T) {
	cfg := marketCalendar(t)
	// 21:00 UTC is 17:00 EDT (rollover) in October; 22:00 UTC is 17:00 EST in January.
	for _, tc := range []struct {
		name      string
		rollover  time.Time
		utcHour   int
		sameLabel bool
	}{
		{"EDT", utc(2026, time.October, 20, 21), 21, true},
		{"EST", utc(2027, time.January, 12, 22), 22, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Two trading days of hourly candles: the first ends exactly at the rollover.
			start := tc.rollover.Add(-48 * time.Hour)
			state := session.Update(bars(start, 72), cfg)
			var pdh *session.Level
			for i := range state.Levels {
				if state.Levels[i].Name == "PDH" {
					pdh = &state.Levels[i]
				}
			}
			if pdh == nil {
				t.Fatal("no previous-day high")
			}
			if !pdh.Final || pdh.AvailableAt%3600 != 0 {
				t.Fatalf("PDH not finalized at an hour boundary: %+v", pdh)
			}
			if got := time.Unix(pdh.AvailableAt, 0).UTC().Hour(); got != tc.utcHour {
				t.Fatalf("the previous trading day closed at %02d:00 UTC, want %02d:00 (17:00 New York)", got, tc.utcHour)
			}
		})
	}
}

func TestADevelopingExtremeIsNeverAFinalizedLevel(t *testing.T) {
	cfg := marketCalendar(t)
	// 11:30 UTC on a summer day: London (07:00-13:00 UTC) is still forming.
	end := utc(2026, time.October, 20, 11)
	state := session.Update(bars(end.Add(-60*time.Hour), 61), cfg)
	for _, l := range state.Levels {
		if !l.Final || l.AvailableAt == 0 || l.AvailableAt > end.Unix() {
			t.Fatalf("a finalized level must be final and already available: %+v", l)
		}
		if l.Name == "LONDON_H" && l.AvailableAt > utc(2026, time.October, 20, 7).Unix() {
			t.Fatalf("the forming London session was reported as a level: %+v", l)
		}
	}
	forming := false
	for _, l := range state.Developing {
		if l.Final || l.AvailableAt != 0 || l.Swept {
			t.Fatalf("a developing extreme must not look finalized: %+v", l)
		}
		forming = forming || l.Name == "LONDON_H"
	}
	if !forming {
		t.Fatal("the forming London session has no developing extreme")
	}
}
