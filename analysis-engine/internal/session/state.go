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
	levels := append(sessionExtremes(candles, cfg), previousDayLevels(candles, cfg)...)
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

	return State{Levels: levels, Developing: developingLevels(candles, cfg), Active: activeSession(candles, cfg)}
}

// sessionExtremes ports session_liquidity.py::_session_extremes: for each
// of Asia/London/NY, walk backward through that window's occurrences
// and emit the high/low levels for the two most recently CLOSED
// occurrences (an occurrence whose close time is still after the latest
// candle is still forming - never reported here, see developingLevels).
// Occurrences follow the configured calendar, so a daylight-saving change moves
// their UTC boundaries.
func sessionExtremes(candles []market.Candle, cfg Config) []Level {
	lastTS := candles[len(candles)-1].Time
	windows := cfg.occurrences(candles[0].Time, lastTS)
	var levels []Level
	for _, name := range []string{Asia, London, NY} {
		closed := 0
		for k := len(windows) - 1; k >= 0 && closed < 2; k-- {
			w := windows[k]
			if w.name != name || w.end > lastTS {
				continue
			}
			run := indicesIn(candles, w.start, w.end)
			if len(run) == 0 {
				continue
			}
			levels = append(levels, finalLevel(levelFromExtreme(candles, run, name+"_H", true, w.end), w.end))
			levels = append(levels, finalLevel(levelFromExtreme(candles, run, name+"_L", false, w.end), w.end))
			closed++
		}
	}
	return levels
}

func finalLevel(l Level, availableAt int64) Level {
	l.Final, l.AvailableAt = true, availableAt
	return l
}

// indicesIn are the positions of the candles opening in [start, end).
func indicesIn(candles []market.Candle, start, end int64) []int {
	var run []int
	for i, c := range candles {
		if c.Time >= start && c.Time < end {
			run = append(run, i)
		}
	}
	return run
}

// developingLevels are the running extremes of the session occurrences and the
// trading day that have not closed as of the latest candle. They are not
// finalized: the next candle can move them, and a sweep of one is not a sweep
// of a level.
func developingLevels(candles []market.Candle, cfg Config) []Level {
	lastTS := candles[len(candles)-1].Time
	var out []Level
	for _, w := range cfg.occurrences(lastTS, lastTS) {
		if !(w.start <= lastTS && lastTS < w.end) {
			continue
		}
		if run := indicesIn(candles, w.start, w.end); len(run) > 0 {
			out = append(out, developingExtreme(candles, run, w.name+"_H", true), developingExtreme(candles, run, w.name+"_L", false))
		}
	}
	_, _, _, rollover := cfg.anchors()
	today := tradingDay(lastTS, rollover)
	var day []int
	for i, c := range candles {
		if tradingDay(c.Time, rollover).Equal(today) {
			day = append(day, i)
		}
	}
	if len(day) > 0 {
		out = append(out, developingExtreme(candles, day, "DAY_H", true), developingExtreme(candles, day, "DAY_L", false))
	}
	return out
}

func developingExtreme(candles []market.Candle, indices []int, name string, isHigh bool) Level {
	l := levelFromExtreme(candles, indices, name, isHigh, candles[len(candles)-1].Time)
	l.Swept, l.SweptAt, l.Final, l.AvailableAt = false, nil, false, 0
	return l
}

// previousDayLevels ports _previous_day_levels: groups candles by
// trading day (rolled over at the configured rollover), and - only once at least
// two distinct trading days exist - emits PDH/PDL for the
// second-to-last one (the last is still the current, possibly still
// forming, trading day).
func previousDayLevels(candles []market.Candle, cfg Config) []Level {
	_, _, _, rollover := cfg.anchors()
	groups := map[time.Time][]int{}
	for i, c := range candles {
		d := tradingDay(c.Time, rollover)
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
	closeTS := dailyCloseTS(previousDay, rollover)
	run := groups[previousDay]
	return []Level{
		finalLevel(levelFromExtreme(candles, run, "PDH", true, closeTS), closeTS),
		finalLevel(levelFromExtreme(candles, run, "PDL", false, closeTS), closeTS),
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
		finalLevel(levelFromExtreme(candles, week, "PWH", true, closedAt), closedAt),
		finalLevel(levelFromExtreme(candles, week, "PWL", false, closedAt), closedAt),
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

// activeSession classifies the latest closed candle into Asia/London/NY -
// new logic (see doc.go), not a port. The occurrences always partition time, so
// exactly one window matches.
func activeSession(candles []market.Candle, cfg Config) string {
	last := candles[len(candles)-1].Time
	for _, w := range cfg.occurrences(last, last) {
		if w.start <= last && last < w.end {
			return w.name
		}
	}
	return ""
}

// dayUTC truncates t to its own UTC calendar day at 00:00 - safe via
// plain Truncate since the Unix epoch itself falls on a UTC midnight
// boundary.
func dayUTC(t int64) time.Time {
	return time.Unix(t, 0).UTC().Truncate(24 * time.Hour)
}

// tradingDay ports _trading_day: a candle at or after the rollover hour
// (wall clock in the rollover anchor's zone) belongs to the FOLLOWING calendar
// date's trading day. The label is a UTC-midnight stand-in for that local date.
func tradingDay(t int64, rollover Anchor) time.Time {
	local := time.Unix(t, 0).In(rollover.Zone)
	label := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC)
	if local.Hour() >= rollover.Hour {
		label = label.AddDate(0, 0, 1)
	}
	return label
}

// dailyCloseTS ports _daily_close_ts: the instant trading day `day` ends, the
// rollover wall clock on that local date.
func dailyCloseTS(day time.Time, rollover Anchor) int64 {
	return rollover.at(day.Year(), day.Month(), day.Day()).Unix()
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
