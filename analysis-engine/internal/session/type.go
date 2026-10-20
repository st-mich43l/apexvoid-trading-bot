package session

import (
	"fmt"
	"sort"
	"time"
	_ "time/tzdata" // the wall-clock zones must resolve on a minimal image

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// Session window names — the "which session is active" classification,
// matching the prefix Level.Name values below (ASIA_H strips to ASIA,
// etc.).
const (
	Asia   = "ASIA"
	London = "LONDON"
	NY     = "NY"
)

// Anchor is a wall-clock hour in an IANA zone: the instant a session (or the
// trading-day rollover) begins on a date is that date's Hour:00 in Zone, so it
// moves in UTC when the zone changes its offset (daylight saving).
type Anchor struct {
	Zone *time.Location
	Hour int
}

// at is the anchor's instant on the local calendar date year-month-day.
func (a Anchor) at(year int, month time.Month, day int) time.Time {
	return time.Date(year, month, day, a.Hour, 0, 0, 0, a.Zone)
}

// Calendar places the three session opens and the daily rollover on real
// market clocks: Asia opens on Tokyo's morning, London and New York on their
// own wall clocks, and the trading day rolls over at New York's 17:00 (the FX
// convention), so every boundary follows its zone's daylight-saving offset.
type Calendar struct {
	Asia, London, NY, Rollover Anchor
}

// NewCalendar resolves the zone names. The tz database is embedded, so a
// minimal container image needs no zoneinfo files.
func NewCalendar(asiaZone string, asiaHour int, londonZone string, londonHour int, nyZone string, nyHour int, rolloverZone string, rolloverHour int) (*Calendar, error) {
	build := func(zone string, hour int) (Anchor, error) {
		if hour < 0 || hour > 23 {
			return Anchor{}, fmt.Errorf("session: hour %d is not a wall-clock hour", hour)
		}
		loc, err := time.LoadLocation(zone)
		if err != nil {
			return Anchor{}, fmt.Errorf("session: zone %q: %w", zone, err)
		}
		return Anchor{Zone: loc, Hour: hour}, nil
	}
	var c Calendar
	var err error
	for _, item := range []struct {
		dst  *Anchor
		zone string
		hour int
	}{{&c.Asia, asiaZone, asiaHour}, {&c.London, londonZone, londonHour}, {&c.NY, nyZone, nyHour}, {&c.Rollover, rolloverZone, rolloverHour}} {
		if *item.dst, err = build(item.zone, item.hour); err != nil {
			return nil, err
		}
	}
	return &c, nil
}

// Config aggregates every session-domain tunable - the same "one
// coherent struct, not scattered parameters" convention
// structure.Settings/zone.Config already establish. AsiaStartHour/
// LondonStartHour/NYStartHour are UTC hours (0-23) reused as-is from
// analysis.sessions.{asia,london,ny}_start; DailyRolloverUTCHour is the
// one new leaf this phase adds (analysis.sessions.daily_rollover_utc_hour,
// Python's real default of 21 - session_liquidity.py::_daily_rollover).
//
// Calendar, when set, replaces those fixed UTC hours with wall-clock anchors
// in their own zones, so the windows and the rollover shift with daylight
// saving. The UTC hours then only serve consumers that still want an integer
// (the MAD Asia phase window).
type Config struct {
	AsiaStartHour        int
	LondonStartHour      int
	NYStartHour          int
	DailyRolloverUTCHour int
	Calendar             *Calendar
}

// window is one session occurrence: [start, end) in Unix seconds, named.
type window struct {
	name       string
	start, end int64
}

// anchors returns the open anchor of each session in cycle order and the
// rollover anchor, in UTC (fixed hours) when no Calendar is set.
func (c Config) anchors() (asia, london, ny, rollover Anchor) {
	if c.Calendar != nil {
		return c.Calendar.Asia, c.Calendar.London, c.Calendar.NY, c.Calendar.Rollover
	}
	return Anchor{time.UTC, c.AsiaStartHour}, Anchor{time.UTC, c.LondonStartHour}, Anchor{time.UTC, c.NYStartHour}, Anchor{time.UTC, c.DailyRolloverUTCHour}
}

// occurrences lists every session occurrence whose open lies within two days of
// [from, to], oldest first. Each occurrence ends where the next session opens,
// so the three partition time. Boundaries are placed on each anchor's own local
// calendar date, which is what makes a daylight-saving change land on the right
// UTC instant.
func (c Config) occurrences(from, to int64) []window {
	asia, london, ny, _ := c.anchors()
	type open struct {
		name  string
		start int64
	}
	var opens []open
	first := time.Unix(from, 0).UTC().AddDate(0, 0, -3)
	last := time.Unix(to, 0).UTC().AddDate(0, 0, 3)
	for _, item := range []struct {
		name   string
		anchor Anchor
	}{{Asia, asia}, {London, london}, {NY, ny}} {
		local := first.In(item.anchor.Zone)
		for d := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, time.UTC); !d.After(last); d = d.AddDate(0, 0, 1) {
			opens = append(opens, open{item.name, item.anchor.at(d.Year(), d.Month(), d.Day()).Unix()})
		}
	}
	sort.SliceStable(opens, func(i, j int) bool { return opens[i].start < opens[j].start })
	out := make([]window, 0, len(opens))
	for i := 0; i+1 < len(opens); i++ {
		out = append(out, window{name: opens[i].name, start: opens[i].start, end: opens[i+1].start})
	}
	return out
}

// Level is one named price level — session_liquidity.py's SessionLevel
// dataclass. Name is one of ASIA_H/ASIA_L/LONDON_H/LONDON_L/NY_H/NY_L/
// PDH/PDL/PWH/PWL, the exact literal tag strings Python uses (nothing
// consuming this data should have to guess the naming convention — see
// doc.go).
type Level struct {
	Name    string
	Price   market.Price
	Time    int64 // the extreme candle's own Time
	Swept   bool
	SweptAt *int64 // nil unless Swept
	// Final is true for a level whose defining period has closed: nothing that
	// trades later can move it. AvailableAt is that close (Unix seconds), the
	// first instant the level is knowable; a decision earlier than it must not
	// use it. A developing extreme (State.Developing) has Final false and no
	// AvailableAt.
	Final       bool
	AvailableAt int64
}

// State is the complete session read for one symbol+timeframe as of the
// latest closed bar — the session analogue of structure.StructureState/
// zone.ZoneState. Levels is a flat slice (mirrors zone.ZoneState.Zones);
// Active names which of Asia/London/NY the latest candle falls in.
type State struct {
	// Levels are the finalized levels only: closed session occurrences, the
	// previous trading day and the previous week.
	Levels []Level
	// Developing are the running extremes of the session occurrences and the
	// trading day that have not closed yet. They move with every candle and are
	// never levels to trade from; they are exposed so a consumer can tell a
	// developing high from a finalized one.
	Developing []Level
	Active     string
}

// Book is the session-domain slice of a symbol's canonical state — same
// shape as structure.Book/zone.Book.
type Book struct {
	ByTimeframe map[market.Timeframe]State
}

// NewBook returns an empty Book ready for Set/Get.
func NewBook() *Book {
	return &Book{ByTimeframe: make(map[market.Timeframe]State)}
}

// Set stores tf's latest State, replacing whatever was there.
func (b *Book) Set(tf market.Timeframe, state State) {
	b.ByTimeframe[tf] = state
}

// Get returns tf's latest State, or the zero value and false if none has
// been computed yet.
func (b *Book) Get(tf market.Timeframe) (State, bool) {
	state, ok := b.ByTimeframe[tf]
	return state, ok
}
