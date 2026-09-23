package session

import (
	"sort"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Update is Session V2's single entrypoint: session extremes (two most
// recently CLOSED windows per Asia/London/NY), prior-day (PDH/PDL) and
// prior-week (PWH/PWL) levels, sweep detection for each, and the active
// session classification for the latest closed bar. Pure and causal,
// same contract as structure.Update/zone.Update: only ever reads the
// candles given to it. candles must be Time-ascending (the same
// convention every other domain package's Update assumes).
func Update(candles []market.Candle, cfg Config) State {
	if len(candles) == 0 {
		return State{}
	}

	// session_liquidity.py::session_levels sorts (extremes + PDH/PDL)
	// by (ts, name, price), then engine.py's own call site appends
	// PWH/PWL unsorted after that — preserved exactly here rather than
	// folding all three into one sort, which would silently reorder
	// weekly levels relative to production's real output shape.
	levels := append(sessionExtremes(candles, cfg), previousDayLevels(candles, cfg.DailyRolloverUTCHour)...)
	sort.SliceStable(levels, func(i, j int) bool {
		if levels[i].Time != levels[j].Time {
			return levels[i].Time < levels[j].Time
		}
		if levels[i].Name != levels[j].Name {
			return levels[i].Name < levels[j].Name
		}
		return levels[i].Price < levels[j].Price
	})
	levels = append(levels, previousWeekLevels(candles)...)

	return State{Levels: levels, Active: activeSession(candles, cfg)}
}

// sessionExtremes ports session_liquidity.py::_session_extremes: for each
// of Asia/London/NY, walk backward through that window's calendar dates
// and emit the high/low levels for the two most recently CLOSED
// occurrences (an occurrence whose close time is still after the latest
// candle is still forming — never reported).
func sessionExtremes(candles []market.Candle, cfg Config) []Level {
	lastTS := candles[len(candles)-1].Time
	var levels []Level
	for _, w := range cfg.windows() {
		groups := map[time.Time][]int{}
		for i, c := range candles {
			hour := hourUTC(c.Time)
			if !inWindow(hour, w.startHour, w.endHour) {
				continue
			}
			d := sessionDate(c.Time, w)
			groups[d] = append(groups[d], i)
		}
		if len(groups) == 0 {
			continue
		}
		days := make([]time.Time, 0, len(groups))
		for d := range groups {
			days = append(days, d)
		}
		sort.Slice(days, func(i, j int) bool { return days[i].After(days[j]) })

		closed := 0
		for _, d := range days {
			closeTS := sessionCloseTS(d, w)
			if closeTS > lastTS {
				continue
			}
			run := groups[d]
			levels = append(levels, levelFromExtreme(candles, run, w.name+"_H", true, closeTS))
			levels = append(levels, levelFromExtreme(candles, run, w.name+"_L", false, closeTS))
			closed++
			if closed >= 2 {
				break
			}
		}
	}
	return levels
}

// previousDayLevels ports _previous_day_levels: groups candles by
// trading day (rolled over at rolloverHour), and — only once at least
// two distinct trading days exist — emits PDH/PDL for the
// second-to-last one (the last is still the current, possibly still
// forming, trading day).
func previousDayLevels(candles []market.Candle, rolloverHour int) []Level {
	groups := map[time.Time][]int{}
	for i, c := range candles {
		d := tradingDay(c.Time, rolloverHour)
		groups[d] = append(groups[d], i)
	}
	if len(groups) < 2 {
		return nil
	}
	days := make([]time.Time, 0, len(groups))
	for d := range groups {
		days = append(days, d)
	}
	sort.Slice(days, func(i, j int) bool { return days[i].Before(days[j]) })
	previousDay := days[len(days)-2]
	closeTS := dailyCloseTS(previousDay, rolloverHour)
	run := groups[previousDay]
	return []Level{
		levelFromExtreme(candles, run, "PDH", true, closeTS),
		levelFromExtreme(candles, run, "PDL", false, closeTS),
	}
}

// previousWeekLevels ports previous_week_levels: PWH/PWL over the
// calendar week (Monday 00:00 UTC boundaries) immediately before the
// week the latest candle falls in — nil if the given candles don't reach
// back that far.
func previousWeekLevels(candles []market.Candle) []Level {
	last := candles[len(candles)-1]
	currentWeek := weekStart(last.Time)
	previousWeek := currentWeek.AddDate(0, 0, -7)
	if candles[0].Time > previousWeek.Unix() {
		return nil
	}
	var week []int
	for i, c := range candles {
		if c.Time >= previousWeek.Unix() && c.Time < currentWeek.Unix() {
			week = append(week, i)
		}
	}
	if len(week) == 0 {
		return nil
	}
	closedAt := candles[week[len(week)-1]].Time
	return []Level{
		levelFromExtreme(candles, week, "PWH", true, closedAt),
		levelFromExtreme(candles, week, "PWL", false, closedAt),
	}
}

// levelFromExtreme ports _level_from_extreme: the first (earliest, on a
// tie — matching pandas idxmax/idxmin) candle in indices carrying the
// window's high or low, plus this level's own sweep status via
// sweptAfter.
func levelFromExtreme(candles []market.Candle, indices []int, name string, isHigh bool, closedAt int64) Level {
	best := indices[0]
	for _, i := range indices[1:] {
		if isHigh {
			if candles[i].High > candles[best].High {
				best = i
			}
		} else if candles[i].Low < candles[best].Low {
			best = i
		}
	}
	var price float64
	if isHigh {
		price = candles[best].High
	} else {
		price = candles[best].Low
	}
	sweptAt := sweptAfter(candles, isHigh, price, closedAt)
	return Level{
		Name:    name,
		Price:   market.Price(price),
		Time:    candles[best].Time,
		Swept:   sweptAt != nil,
		SweptAt: sweptAt,
	}
}

// sweptAfter ports _swept_ts: the first candle strictly after closedAt
// whose high exceeds (for a high level) or low undercuts (for a low
// level) price — nil if never swept within candles.
func sweptAfter(candles []market.Candle, isHigh bool, price float64, closedAt int64) *int64 {
	for _, c := range candles {
		if c.Time <= closedAt {
			continue
		}
		if isHigh {
			if c.High > price {
				t := c.Time
				return &t
			}
		} else if c.Low < price {
			t := c.Time
			return &t
		}
	}
	return nil
}

// activeSession classifies the latest closed candle into Asia/London/NY
// — new logic (see doc.go), not a port. windows() always partitions the
// full 24h UTC day, so exactly one window matches.
func activeSession(candles []market.Candle, cfg Config) string {
	hour := hourUTC(candles[len(candles)-1].Time)
	for _, w := range cfg.windows() {
		if inWindow(hour, w.startHour, w.endHour) {
			return w.name
		}
	}
	return ""
}

func hourUTC(t int64) int {
	return time.Unix(t, 0).UTC().Hour()
}

// dayUTC truncates t to its own UTC calendar day at 00:00 — safe via
// plain Truncate since the Unix epoch itself falls on a UTC midnight
// boundary.
func dayUTC(t int64) time.Time {
	return time.Unix(t, 0).UTC().Truncate(24 * time.Hour)
}

// inWindow ports _in_window: a window may wrap past midnight (start >
// end, e.g. NY 13->22 wrapping is not the case, but ASIA/others can
// depending on config), same-hour comparison either way.
func inWindow(hour, start, end int) bool {
	if start < end {
		return start <= hour && hour < end
	}
	return hour >= start || hour < end
}

// sessionDate ports _session_date: a wrapping window's pre-midnight
// hours belong to the PRECEDING calendar date's occurrence of that
// window.
func sessionDate(t int64, w window) time.Time {
	d := dayUTC(t)
	if w.startHour > w.endHour && hourUTC(t) < w.endHour {
		return d.AddDate(0, 0, -1)
	}
	return d
}

// sessionCloseTS ports _session_close_ts.
func sessionCloseTS(d time.Time, w window) int64 {
	closeDate := d
	if w.startHour > w.endHour {
		closeDate = d.AddDate(0, 0, 1)
	}
	return time.Date(closeDate.Year(), closeDate.Month(), closeDate.Day(), w.endHour, 0, 0, 0, time.UTC).Unix()
}

// tradingDay ports _trading_day: a candle at or after rolloverHour
// belongs to the FOLLOWING calendar date's trading day.
func tradingDay(t int64, rolloverHour int) time.Time {
	if hourUTC(t) >= rolloverHour {
		return dayUTC(t).AddDate(0, 0, 1)
	}
	return dayUTC(t)
}

// dailyCloseTS ports _daily_close_ts.
func dailyCloseTS(day time.Time, rolloverHour int) int64 {
	return time.Date(day.Year(), day.Month(), day.Day(), rolloverHour, 0, 0, 0, time.UTC).Unix()
}

// weekStart ports _week_start: Monday 00:00 UTC of t's own calendar
// week. Go's time.Weekday is Sunday=0..Saturday=6; Python's
// ts.weekday() is Monday=0..Sunday=6, so it is converted before
// subtracting.
func weekStart(t int64) time.Time {
	d := dayUTC(t)
	pyWeekday := (int(d.Weekday()) + 6) % 7
	return d.AddDate(0, 0, -pyWeekday)
}
