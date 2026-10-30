package session

import "time"

// The FX/metals weekly closure: from Friday 17:00 to Sunday 17:00 on the rollover clock (New York by default), when no bar exists. No instrument in
// config/instruments.yml declares a daily maintenance break, so this is the only closure; a holiday that closes the market longer shows up as a gap.
const (
	closeWeekday = time.Friday
	openWeekday  = time.Sunday
)

// MarketClosed reports whether the bar opening at t falls in the weekly closure (Friday 17:00 to Sunday 17:00 on the rollover clock).
func (c Config) MarketClosed(t int64) bool {
	_, _, _, rollover := c.anchors()
	local := time.Unix(t, 0).In(rollover.Zone)
	switch local.Weekday() {
	case time.Saturday:
		return true
	case closeWeekday:
		return local.Hour() >= rollover.Hour
	case openWeekday:
		return local.Hour() < rollover.Hour
	}
	return false
}

// Gaps returns the open time of the first missing bar of every hole in a closed-bar series of the given period (seconds): consecutive bars more than one
// period apart where at least one of the missing bars would have opened while the market was open. A series is Time-ascending.
func (c Config) Gaps(times []int64, period int64) []int64 {
	if period <= 0 {
		return nil
	}
	var gaps []int64
	for i := 1; i < len(times); i++ {
		if times[i]-times[i-1] <= period {
			continue
		}
		for expected := times[i-1] + period; expected < times[i]; expected += period {
			if !c.MarketClosed(expected) {
				gaps = append(gaps, expected)
				break
			}
		}
	}
	return gaps
}
