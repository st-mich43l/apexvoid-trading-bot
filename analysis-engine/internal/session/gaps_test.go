package session

import (
	"testing"
	"time"
)

func productionCalendar(t *testing.T) Config {
	t.Helper()
	calendar, err := NewCalendar("Asia/Tokyo", 7, "Europe/London", 8, "America/New_York", 9, "America/New_York", 17)
	if err != nil {
		t.Fatal(err)
	}
	return Config{AsiaStartHour: 22, LondonStartHour: 7, NYStartHour: 13, DailyRolloverUTCHour: 21, Calendar: calendar}
}

func TestTheWeeklyClosureRunsFromFridayToSundayRolloverAndFollowsDaylightSaving(t *testing.T) {
	cfg := productionCalendar(t)
	at := func(y int, m time.Month, d, h, min int) int64 {
		return time.Date(y, m, d, h, min, 0, 0, time.UTC).Unix()
	}
	for _, c := range []struct {
		name   string
		t      int64
		closed bool
	}{
		{"Friday 16:55 New York (20:55 UTC in EDT) is open", at(2026, 10, 23, 20, 55), false},
		{"Friday 17:00 New York (21:00 UTC in EDT) is closed", at(2026, 10, 23, 21, 0), true},
		{"Saturday is closed", at(2026, 10, 24, 12, 0), true},
		{"Sunday 16:55 New York is closed", at(2026, 10, 25, 20, 55), true},
		{"Sunday 17:00 New York (21:00 UTC in EDT) is open", at(2026, 10, 25, 21, 0), false},
		{"Monday is open", at(2026, 10, 26, 3, 0), false},
		{"after the change Friday 17:00 New York is 22:00 UTC: 21:00 UTC is still open", at(2026, 11, 6, 21, 0), false},
		{"after the change Friday 22:00 UTC is closed", at(2026, 11, 6, 22, 0), true},
		{"after the change Sunday 21:00 UTC is closed", at(2026, 11, 8, 21, 0), true},
		{"after the change Sunday 22:00 UTC is open", at(2026, 11, 8, 22, 0), false},
	} {
		if got := cfg.MarketClosed(c.t); got != c.closed {
			t.Errorf("%s: MarketClosed = %v", c.name, got)
		}
	}
}

func TestGapsAreHolesInsideTheOpenMarketOnly(t *testing.T) {
	cfg := productionCalendar(t)
	const m5 = 300
	start := time.Date(2026, 10, 22, 8, 0, 0, 0, time.UTC).Unix() // Thursday
	var times []int64
	for i := 0; i < 20; i++ {
		times = append(times, start+int64(i)*m5)
	}
	if got := cfg.Gaps(times, m5); len(got) != 0 {
		t.Fatalf("a continuous series has no gap: %v", got)
	}
	// two missing bars in the middle of Thursday
	holed := append(append([]int64{}, times[:10]...), times[12:]...)
	if got := cfg.Gaps(holed, m5); len(got) != 1 || got[0] != times[10] {
		t.Fatalf("gaps %v, want one starting at %d", got, times[10])
	}
	// the weekend: Friday 20:55 UTC then Sunday 21:00 UTC (EDT)
	weekend := []int64{time.Date(2026, 10, 23, 20, 55, 0, 0, time.UTC).Unix(), time.Date(2026, 10, 25, 21, 0, 0, 0, time.UTC).Unix()}
	if got := cfg.Gaps(weekend, m5); len(got) != 0 {
		t.Fatalf("the weekly closure is not a gap: %v", got)
	}
	// a missing Sunday-evening open is
	late := []int64{weekend[0], time.Date(2026, 10, 25, 21, 15, 0, 0, time.UTC).Unix()}
	if got := cfg.Gaps(late, m5); len(got) != 1 || got[0] != weekend[1] {
		t.Fatalf("gaps %v, want the missed 21:00 open", got)
	}
}
