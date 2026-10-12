package sessionlevel

import (
	"fmt"
	"math"
	"sort"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

const (
	h1Seconds = int64(3600)
	hour      = int64(3600)
)

// window is one session's UTC hour range. The three windows partition the day:
// each ends where the next starts, and the last wraps to the first.
type window struct {
	name       string
	start, end int // UTC hours
}

func (c Config) windows() []window {
	return []window{
		{"ASIA", c.AsiaStartHour, c.LondonStartHour},
		{"LONDON", c.LondonStartHour, c.NYStartHour},
		{"NY", c.NYStartHour, c.AsiaStartHour},
	}
}

// occurrence is one dated run of a session window: [start, end) in Unix seconds.
func (w window) occurrence(day time.Time) (start, end int64) {
	start = day.Add(time.Duration(w.start) * time.Hour).Unix()
	length := (w.end - w.start + 24) % 24
	if length == 0 {
		length = 24
	}
	return start, start + int64(length)*hour
}

func dayUTC(t int64) time.Time { return time.Unix(t, 0).UTC().Truncate(24 * time.Hour) }

// tradingDay is the trading day a candle belongs to: a candle at or after the
// rollover hour belongs to the FOLLOWING calendar date, so Sunday evening's first
// candles are Monday's and Friday's session is labelled Friday.
func tradingDay(t int64, rollover int) time.Time {
	if time.Unix(t, 0).UTC().Hour() >= rollover {
		return dayUTC(t).AddDate(0, 0, 1)
	}
	return dayUTC(t)
}

// dayBounds are a trading day's own [start, end): the rollover hour before its
// label to the rollover hour on it.
func dayBounds(label time.Time, rollover int) (start, end int64) {
	end = label.Add(time.Duration(rollover) * time.Hour).Unix()
	return end - 24*hour, end
}

// weekKey is the ISO week of a trading-day label.
func weekKey(label time.Time) int {
	year, week := label.ISOWeek()
	return year*100 + week
}

// weekBounds are the trading week's [start, end): the rollover hour before Monday's
// label to the rollover hour on Friday's label.
func weekBounds(monday time.Time, rollover int) (start, end int64) {
	start = monday.Add(time.Duration(rollover)*time.Hour).Unix() - 24*hour
	end = monday.AddDate(0, 0, 4).Add(time.Duration(rollover) * time.Hour).Unix()
	return start, end
}

type tolerances struct {
	startMin, endMin, gapMin int
	coverage                 float64
}

// periodExtremes reads the defining candles of [start, end) from bars (period
// seconds each) and refuses the period unless the candles really cover it: the first
// candle is within the start tolerance, the last candle's close within the end
// tolerance, no hole inside is longer than the allowed gap, and the candles fill
// enough of the period. It returns the indices of the highest high and lowest low
// (the earliest on a tie) and the coverage.
func periodExtremes(bars []market.Candle, seconds, start, end int64, tol tolerances) (hi, lo, count int, coverage float64, reason string) {
	first, last := -1, -1
	for i, c := range bars {
		if c.Time >= start && c.Time+seconds <= end {
			if first < 0 {
				first = i
			}
			last = i
		}
	}
	if first < 0 {
		return 0, 0, 0, 0, ReasonNoCandles
	}
	if bars[first].Time-start > int64(tol.startMin)*60 {
		return 0, 0, 0, 0, ReasonStartIncomplete
	}
	if end-(bars[last].Time+seconds) > int64(tol.endMin)*60 {
		return 0, 0, 0, 0, ReasonEndIncomplete
	}
	hi, lo = first, first
	for i := first; i <= last; i++ {
		if i > first && bars[i].Time-bars[i-1].Time-seconds > int64(tol.gapMin)*60 {
			return 0, 0, 0, 0, ReasonFeedGap
		}
		count++
		if bars[i].High > bars[hi].High {
			hi = i
		}
		if bars[i].Low < bars[lo].Low {
			lo = i
		}
	}
	coverage = float64(count) * float64(seconds) / float64(end-start)
	if coverage+1e-12 < tol.coverage {
		return 0, 0, 0, coverage, ReasonLowCoverage
	}
	return hi, lo, count, coverage, ""
}

// buildLevels builds every reference level that is complete and still current as
// of now (the close time of the evaluated M5 candle), and records every period it
// refused. Only the LATEST closed occurrence of each reference is considered: an
// older one is superseded, never a fallback for an incomplete newer one.
func buildLevels(cfg Config, m5, h1 []market.Candle, now int64) (levels []Level, rejected []Rejected) {
	seen := map[string]bool{}
	add := func(l Level) {
		if !seen[l.ID] {
			seen[l.ID] = true
			levels = append(levels, l)
		}
	}
	sessionTol := tolerances{cfg.SessionStartToleranceMin, cfg.SessionEndToleranceMin, cfg.SessionMaxGapMin, cfg.SessionMinCoverage}
	periodTol := tolerances{cfg.PeriodStartToleranceMin, cfg.PeriodEndToleranceMin, cfg.PeriodMaxGapMin, cfg.PeriodMinCoverage}

	// Sessions, from M5: the latest occurrence of each window that has closed.
	today := dayUTC(now)
	for _, w := range cfg.windows() {
		var bestStart, bestEnd int64 = 0, 0
		for back := 0; back <= 3; back++ {
			start, end := w.occurrence(today.AddDate(0, 0, -back))
			if end <= now && end > bestEnd {
				bestStart, bestEnd = start, end
			}
		}
		if bestEnd == 0 {
			continue
		}
		name := w.name
		if float64(now-bestEnd) > cfg.SessionMaxAgeHours*3600 {
			rejected = append(rejected, Rejected{Name: name, Kind: KindSession, Start: bestStart, End: bestEnd, Reason: ReasonTooOld})
			continue
		}
		hi, lo, count, coverage, reason := periodExtremes(m5, m5Seconds, bestStart, bestEnd, sessionTol)
		if reason != "" {
			rejected = append(rejected, Rejected{Name: name, Kind: KindSession, Start: bestStart, End: bestEnd, Reason: reason})
			continue
		}
		add(levelOf(name+"_H", KindSession, SideResistance, m5[hi].High, bestStart, bestEnd, market.M5, m5[hi].Time, count, coverage))
		add(levelOf(name+"_L", KindSession, SideSupport, m5[lo].Low, bestStart, bestEnd, market.M5, m5[lo].Time, count, coverage))
	}

	// Previous trading day and previous trading week, from H1.
	if len(h1) == 0 {
		rejected = append(rejected, Rejected{Name: "PD", Kind: KindDay, Reason: ReasonNoHigherTF}, Rejected{Name: "PW", Kind: KindWeek, Reason: ReasonNoHigherTF})
		return levels, rejected
	}
	current := tradingDay(now-1, cfg.RolloverHour)
	labels := map[int64][]int{} // trading-day label -> H1 indices
	for i, c := range h1 {
		label := tradingDay(c.Time, cfg.RolloverHour)
		labels[label.Unix()] = append(labels[label.Unix()], i)
	}
	var days []int64
	for label := range labels {
		days = append(days, label)
	}
	sort.Slice(days, func(i, j int) bool { return days[i] < days[j] })

	// Previous trading day: the latest label before the current one that has candles.
	for k := len(days) - 1; k >= 0; k-- {
		if days[k] >= current.Unix() {
			continue
		}
		label := time.Unix(days[k], 0).UTC()
		start, end := dayBounds(label, cfg.RolloverHour)
		if end > now {
			continue
		}
		if float64(now-end) > cfg.DayMaxAgeHours*3600 {
			rejected = append(rejected, Rejected{Name: "PD", Kind: KindDay, Start: start, End: end, Reason: ReasonTooOld})
			break
		}
		hi, lo, count, coverage, reason := periodExtremes(h1, h1Seconds, start, end, periodTol)
		if reason != "" {
			rejected = append(rejected, Rejected{Name: "PD", Kind: KindDay, Start: start, End: end, Reason: reason})
			break
		}
		add(levelOf("PDH", KindDay, SideResistance, h1[hi].High, start, end, market.H1, h1[hi].Time, count, coverage))
		add(levelOf("PDL", KindDay, SideSupport, h1[lo].Low, start, end, market.H1, h1[lo].Time, count, coverage))
		break
	}

	// Previous trading week: the latest ISO week before the current one with candles.
	currentWeek := weekKey(current)
	byWeek := map[int]time.Time{} // week key -> that week's Monday label
	for _, label := range days {
		t := time.Unix(label, 0).UTC()
		key := weekKey(t)
		monday := t.AddDate(0, 0, -((int(t.Weekday()) + 6) % 7))
		byWeek[key] = monday
	}
	var weeks []int
	for key := range byWeek {
		weeks = append(weeks, key)
	}
	sort.Ints(weeks)
	for k := len(weeks) - 1; k >= 0; k-- {
		if weeks[k] >= currentWeek {
			continue
		}
		start, end := weekBounds(byWeek[weeks[k]], cfg.RolloverHour)
		if end > now {
			continue
		}
		if float64(now-end) > cfg.WeekMaxAgeHours*3600 {
			rejected = append(rejected, Rejected{Name: "PW", Kind: KindWeek, Start: start, End: end, Reason: ReasonTooOld})
			break
		}
		hi, lo, count, coverage, reason := periodExtremes(h1, h1Seconds, start, end, periodTol)
		if reason != "" {
			rejected = append(rejected, Rejected{Name: "PW", Kind: KindWeek, Start: start, End: end, Reason: reason})
			break
		}
		add(levelOf("PWH", KindWeek, SideResistance, h1[hi].High, start, end, market.H1, h1[hi].Time, count, coverage))
		add(levelOf("PWL", KindWeek, SideSupport, h1[lo].Low, start, end, market.H1, h1[lo].Time, count, coverage))
		break
	}
	sort.SliceStable(levels, func(i, j int) bool { return levels[i].ID < levels[j].ID })
	return levels, rejected
}

func levelOf(name, kind, side string, price float64, start, end int64, tf market.Timeframe, formed int64, count int, coverage float64) Level {
	return Level{
		ID: fmt.Sprintf("level:%s:%d", name, start), Name: name, Kind: kind, Side: side, Price: price,
		PeriodStart: start, PeriodEnd: end, SourceTF: tf, FormedAt: formed, AvailableAt: end, Candles: count, Coverage: math.Round(coverage*1e6) / 1e6,
	}
}
